"""
executor.py — the part that actually does things, safely.

Responsibilities:
  * refuse to repeat work (idempotency keys)
  * run every proposed step past the guardrails
  * ask the oracle what happened
  * write an audit line for every decision, including the ones we blocked
  * park anything unresolvable in a dead-letter queue for a human

Nothing here is clever. In payments, that is the goal.
"""

from datetime import datetime, timedelta

from . import guardrails
from .guardrails import State


def _next_lawful_time(rule: str, state, at: datetime, payment):
    """
    For timing-based rules only, return the earliest moment the action would
    become permitted. Returns None for rules that can never be waited out
    (attempt caps, spend caps, permanent declines, the kill switch).
    """
    if rule == "quiet_hours":
        start, end = guardrails.QUIET_HOURS
        if at.hour < start:
            return at.replace(hour=start, minute=0, second=0, microsecond=0)
        return (at + timedelta(days=1)).replace(
            hour=start, minute=0, second=0, microsecond=0)

    if rule == "cool_off" and state.last_charge_at:
        return state.last_charge_at + timedelta(hours=guardrails.CHARGE_COOL_OFF_HOURS)

    if rule == "pre_debit_notice" and state.notice_sent_at:
        return state.notice_sent_at + timedelta(hours=guardrails.PRE_DEBIT_NOTICE_HOURS)

    return None


class Executor:
    def __init__(self, oracle, enforce_guardrails: bool = True):
        # The baseline runs with guardrails off. Hobbling it with rules it
        # was never designed to respect would make the comparison dishonest —
        # we want to beat the real thing, at full strength.
        self.enforce_guardrails = enforce_guardrails
        self.oracle = oracle
        self.audit = []            # every decision, in order
        self.dead_letter = []      # could not be processed — needs a human
        self.exhausted = []        # processed fine, simply not recovered
        self._done_keys = set()    # idempotency

    # -- audit ---------------------------------------------------------------

    def _log(self, payment_id, at, action, outcome, detail, amount=0.0, cost=0.0):
        self.audit.append({
            "payment_id": payment_id,
            "at": at.isoformat(),
            "action": action,
            "outcome": outcome,          # executed | blocked | skipped | recovered
            "detail": detail,
            "recovered_rupees": amount,
            "cost_rupees": cost,
        })

    # -- one payment ---------------------------------------------------------

    def run(self, payment, steps, plan_reason=None, label="reflow"):
        """
        Walk a plan for one payment. Stops at the first success.

        Returns a result dict used by the evaluator.
        """
        failed_at = datetime.fromisoformat(payment.failed_at)
        state = State()
        nudges_sent = 0

        if not steps:
            reason = plan_reason or "no plan produced"
            self._log(payment.id, failed_at, "none", "skipped", reason)
            if plan_reason and "confidence" in plan_reason:
                self.dead_letter.append({"payment_id": payment.id, "reason": reason})
            return {
                "payment_id": payment.id, "recovered": False, "amount": 0.0,
                "attempts": 0, "nudges": 0, "cost": 0.0, "exception": reason,
                "unaided": False,
            }

        for step in steps:
            at = failed_at + timedelta(hours=step.delay_hours)

            # The customer may simply have come back and paid. Both policies
            # get credited for this equally — but only the one that held its
            # fire avoids paying to message someone already converted.
            if self.oracle.self_recovered_by(payment, at):
                self._log(payment.id, at, "none", "self_recovered",
                          "customer completed payment unaided — standing down",
                          amount=payment.amount_rupees())
                return {
                    "payment_id": payment.id, "recovered": True,
                    "amount": payment.amount_rupees(), "attempts": state.attempts,
                    "nudges": nudges_sent, "cost": state.spend,
                    "exception": None, "unaided": True,
                }

            # Idempotency: the same action for the same payment at the same
            # planned offset must never run twice, however many times this
            # process restarts.
            key = f"{label}:{payment.id}:{step.action}:{step.delay_hours}"
            if key in self._done_keys:
                self._log(payment.id, at, step.action, "skipped",
                          "idempotency key already settled")
                continue

            # Guardrails come in two flavours. A hard rule ends the step.
            # A timing rule means "not yet" — so we move the action to the
            # next lawful moment rather than throwing the recovery away.
            # Blocking a compliant action is a bug, not compliance.
            deferrals = 0
            while self.enforce_guardrails and deferrals < 3:
                verdict = guardrails.check(step, state, at, payment)
                if verdict.allowed:
                    break
                later = _next_lawful_time(verdict.rule, state, at, payment)
                if later is None or later <= at:
                    break
                self._log(payment.id, at, step.action, "deferred",
                          f"{verdict.rule}: {verdict.reason} -> retimed to "
                          f"{later.strftime('%d %b %H:%M')}")
                at = later
                deferrals += 1

            verdict = (guardrails.check(step, state, at, payment)
                       if self.enforce_guardrails else guardrails.Verdict(True))
            if not verdict.allowed:
                self._log(payment.id, at, step.action, "blocked",
                          f"{verdict.rule}: {verdict.reason}")
                continue

            self._done_keys.add(key)
            state.attempts += 1
            state.spend += step.cost
            if step.kind == "charge":
                state.charge_attempts += 1
                state.last_charge_at = at
            else:
                nudges_sent += 1
                state.notice_sent_at = at

            oracle_action = "charge" if step.kind == "charge" else "nudge"
            success = self.oracle.attempt(payment, oracle_action, at)

            if success:
                self._log(payment.id, at, step.action, "recovered", step.note,
                          amount=payment.amount_rupees(), cost=step.cost)
                return {
                    "payment_id": payment.id, "recovered": True,
                    "amount": payment.amount_rupees(),
                    "attempts": state.attempts, "nudges": nudges_sent,
                    "cost": state.spend, "exception": None, "unaided": False,
                }

            self._log(payment.id, at, step.action, "executed",
                      f"{step.note} — no success", cost=step.cost)

        # NOTE: this is NOT a dead letter. The system processed this payment
        # correctly and simply could not recover it — an ordinary business
        # outcome. The dead-letter queue is reserved for payments the system
        # could not process at all (unreadable diagnosis, unhandled error).
        # Conflating the two would inflate the dead-letter number and hide
        # real processing failures.
        exception = "plan exhausted without recovery"
        self.exhausted.append({"payment_id": payment.id, "reason": exception})
        return {
            "payment_id": payment.id, "recovered": False, "amount": 0.0,
            "attempts": state.attempts, "nudges": nudges_sent,
            "cost": state.spend, "exception": exception,
        }


# --- the thing we are trying to beat ---------------------------------------

from .policy import Step   # noqa: E402  (kept here to show the contrast)

BASELINE_PLAN = [
    Step("charge", 0.5, "blind retry 1"),
    Step("charge", 6.0, "blind retry 2"),
    Step("charge", 24.0, "blind retry 3"),
    Step("nudge_sms", 25.0, "blanket reminder to every failed payer"),
]


def baseline_steps(payment):
    """
    Industry-standard dunning: three spaced retries and one blanket SMS to
    every failed payer. No diagnosis, no mandate check, no stopping rule,
    no quiet hours.

    This is deliberately the strong version of the incumbent, not a
    strawman. Beating a weak opponent proves nothing.
    """
    return list(BASELINE_PLAN)
