"""
guardrails.py — compliance written as code, not as a README paragraph.

Every rule is a function that inspects a proposed action and either allows it
or blocks it with a stated reason. Nothing reaches the executor without
passing all of them, and every block is written to the audit trail.

The rules encoded here reflect real constraints on Indian payment operations:
messaging time windows, attempt caps, cool-off after declines, pre-debit
notification for recurring mandates, and a per-payment spend ceiling.

These are illustrative implementations for a simulation, not legal advice —
but the shape is the point: a rule you can run is a rule you can prove.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_ATTEMPTS = 3              # total actions per payment
MAX_CHARGE_ATTEMPTS = 2       # of which, charges
CHARGE_COOL_OFF_HOURS = 2.0
QUIET_HOURS = (9, 21)         # messaging permitted 09:00–21:00 local
MAX_SPEND_PER_PAYMENT = 1.00  # rupees
PRE_DEBIT_NOTICE_HOURS = 24   # recurring mandates need advance notice
GLOBAL_KILL_SWITCH = False    # flip to True to halt all outbound actions


@dataclass
class Verdict:
    allowed: bool
    rule: str = ""
    reason: str = ""


@dataclass
class State:
    """Everything the rules need to know about a payment so far."""
    attempts: int = 0
    charge_attempts: int = 0
    spend: float = 0.0
    last_charge_at: datetime | None = None
    notice_sent_at: datetime | None = None
    hard_declined: bool = False


# --- Individual rules ------------------------------------------------------

def rule_kill_switch(step, state, at, payment) -> Verdict:
    if GLOBAL_KILL_SWITCH:
        return Verdict(False, "kill_switch", "global kill switch is engaged")
    return Verdict(True)


def rule_attempt_cap(step, state, at, payment) -> Verdict:
    if state.attempts >= MAX_ATTEMPTS:
        return Verdict(False, "attempt_cap",
                       f"already made {state.attempts} attempts (cap {MAX_ATTEMPTS})")
    if step.kind == "charge" and state.charge_attempts >= MAX_CHARGE_ATTEMPTS:
        return Verdict(False, "charge_cap",
                       f"charge cap of {MAX_CHARGE_ATTEMPTS} reached")
    return Verdict(True)


def rule_hard_decline_block(step, state, at, payment) -> Verdict:
    if state.hard_declined and step.kind == "charge":
        return Verdict(False, "hard_decline_block",
                       "issuer issued a permanent decline; further charges prohibited")
    return Verdict(True)


def rule_charge_cool_off(step, state, at, payment) -> Verdict:
    if step.kind != "charge" or state.last_charge_at is None:
        return Verdict(True)
    gap = (at - state.last_charge_at).total_seconds() / 3600
    if gap < CHARGE_COOL_OFF_HOURS:
        return Verdict(False, "cool_off",
                       f"only {gap:.1f}h since last charge (min {CHARGE_COOL_OFF_HOURS}h)")
    return Verdict(True)


def rule_quiet_hours(step, state, at, payment) -> Verdict:
    if step.kind != "nudge":
        return Verdict(True)
    start, end = QUIET_HOURS
    if not (start <= at.hour < end):
        return Verdict(False, "quiet_hours",
                       f"messaging blocked at {at.hour:02d}:00 "
                       f"(window {start:02d}:00–{end:02d}:00)")
    return Verdict(True)


def rule_spend_cap(step, state, at, payment) -> Verdict:
    if state.spend + step.cost > MAX_SPEND_PER_PAYMENT:
        return Verdict(False, "spend_cap",
                       f"would spend Rs {state.spend + step.cost:.2f} "
                       f"(cap Rs {MAX_SPEND_PER_PAYMENT:.2f})")
    return Verdict(True)


def rule_pre_debit_notice(step, state, at, payment) -> Verdict:
    """Recurring mandates require advance notice before a debit."""
    if step.kind != "charge" or payment.method != "emandate":
        return Verdict(True)
    if state.notice_sent_at is None:
        return Verdict(False, "pre_debit_notice",
                       "no pre-debit notification on record for this mandate")
    gap = (at - state.notice_sent_at).total_seconds() / 3600
    if gap < PRE_DEBIT_NOTICE_HOURS:
        return Verdict(False, "pre_debit_notice",
                       f"notice sent only {gap:.1f}h ago "
                       f"(min {PRE_DEBIT_NOTICE_HOURS}h)")
    return Verdict(True)


def rule_economic_sanity(step, state, at, payment) -> Verdict:
    """Never spend more chasing a payment than the payment is worth."""
    if step.cost > payment.amount_rupees() * 0.25:
        return Verdict(False, "economic_sanity",
                       "recovery cost exceeds 25% of the amount at risk")
    return Verdict(True)


RULES = [
    rule_kill_switch,
    rule_attempt_cap,
    rule_hard_decline_block,
    rule_charge_cool_off,
    rule_quiet_hours,
    rule_spend_cap,
    rule_pre_debit_notice,
    rule_economic_sanity,
]


def check(step, state: State, at: datetime, payment) -> Verdict:
    """Run every rule. First block wins, and the reason is recorded."""
    for rule in RULES:
        verdict = rule(step, state, at, payment)
        if not verdict.allowed:
            return verdict
    return Verdict(True)
