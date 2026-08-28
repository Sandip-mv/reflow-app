"""
simulator.py — builds the fake world.

This file does two jobs:

1. make_cohort()  -> invents N failed payments, each with a messy gateway
                     error string (this is ALL the agent gets to see).
2. Oracle         -> the secret answer key. It knows, for every payment,
                     whether a given action at a given time would actually
                     have worked. The agent never imports this knowledge
                     directly; it only asks "did this action succeed?".

Why this design: we cannot test on real money, so we build a world where
the counterfactual ("what would have happened if we retried at 11:45
instead of 10:30?") has a defined, repeatable answer.

Randomness is seeded per (payment, action, time-bucket), so the oracle
gives the SAME answer no matter what order policies ask questions in.
That is what makes the baseline-vs-Reflow comparison fair.
"""

import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# The seven failure causes we model, and the messy strings gateways emit.
# Multiple phrasings per cause on purpose: the diagnosis layer has to do
# real work, not a dictionary lookup.
# ---------------------------------------------------------------------------

RAW_ERRORS = {
    "issuer_downtime": [
        "GATEWAY_ERROR: bank_error_code=U69 desc=payer psp unavailable",
        "BANK_DECLINE code=91 issuer switch inoperative, please retry later",
        "upi_error: RB / remitter bank offline for maintenance window",
        "acquirer response 5003 - issuing bank unreachable (timeout at switch)",
    ],
    "insufficient_funds": [
        "DECLINED code=51 INSUFFICIENT FUNDS",
        "bank_error_code=U30 debit has failed - low balance in account",
        "NPCI RC: 116 - Not sufficient funds available for this txn",
        "payment failed: available balance less than transaction amount",
    ],
    "checkout_abandon": [
        "PAYMENT_TIMEOUT: customer did not complete 3DS authentication",
        "user dropped off at OTP page, session expired after 300s",
        "3ds_challenge_abandoned - no ACS response received from cardholder",
        "collect request expired, payer did not approve within TTL",
    ],
    "network_timeout": [
        "GATEWAY_TIMEOUT: no response from acquirer after 30000ms",
        "socket hang up while awaiting authorization response",
        "TXN_STATUS_UNKNOWN - reversal initiated, please retry",
    ],
    "expired_instrument": [
        "DECLINED code=54 EXPIRED CARD",
        "card_expired: the card used for this payment has expired (0824)",
        "token invalid - saved instrument past expiry, re-collect required",
    ],
    "mandate_expired": [
        "emandate_error: mandate not active / debit window breached",
        "SI_FAILED - standing instruction revoked or lapsed for this token",
        "recurring debit rejected: umn not found at destination bank",
    ],
    "hard_decline": [
        "DECLINED code=43 STOLEN CARD - do not honour",
        "bank_error_code=U16 account closed by customer",
        "RISK_BLOCK: issuer permanent decline, do not retry (code 04)",
        "do_not_honour - blocked by issuer fraud rules",
    ],
}

# How common each cause is. Roughly mirrors what Indian PGs actually see:
# soft, recoverable failures dominate; permanent declines are the minority.
CAUSE_WEIGHTS = {
    "issuer_downtime": 0.20,
    "insufficient_funds": 0.22,
    "checkout_abandon": 0.24,
    "network_timeout": 0.12,
    "expired_instrument": 0.08,
    "mandate_expired": 0.07,
    "hard_decline": 0.07,
}

METHOD_BY_CAUSE = {
    "issuer_downtime": ["upi", "netbanking", "card"],
    "insufficient_funds": ["upi", "card", "emandate"],
    "checkout_abandon": ["card", "upi", "netbanking"],
    "network_timeout": ["upi", "card", "netbanking"],
    "expired_instrument": ["card"],
    "mandate_expired": ["emandate"],
    "hard_decline": ["card", "upi"],
}

# Can the merchant charge again on its own, or does it need the customer?
# This is the single most under-modelled fact in naive retry systems.
MERCHANT_CAN_CHARGE = {"card_on_file", "emandate"}


@dataclass
class Payment:
    """What the agent is allowed to see."""
    id: str
    amount_paise: int
    method: str
    has_mandate: bool          # merchant may charge without customer action
    failed_at: str             # ISO timestamp
    raw_error: str
    customer_hint: str         # deliberately contains PII, to test redaction
    prior_success_count: int   # how many times this customer has paid before

    def amount_rupees(self) -> float:
        return self.amount_paise / 100.0


@dataclass
class Truth:
    """The secret answer key. Never passed to the agent."""
    payment_id: str
    cause: str
    outage_ends_at: str | None = None      # issuer_downtime
    funds_arrive_at: str | None = None     # insufficient_funds
    self_recovers_at: str | None = None    # customer would have paid anyway
    nudge_conversion: float = 0.0          # chance a payment link converts
    recoverable: bool = True


def _rng(*parts) -> random.Random:
    """Deterministic RNG keyed on whatever we pass in."""
    return random.Random("|".join(str(p) for p in parts))


def make_cohort(n: int = 500, seed: int = 7):
    """Invent n failed payments plus the hidden truth for each."""
    rnd = random.Random(seed)
    t0 = datetime(2026, 9, 1, 9, 0, 0)

    causes = list(CAUSE_WEIGHTS)
    weights = [CAUSE_WEIGHTS[c] for c in causes]

    payments, truths = [], {}
    for i in range(n):
        pid = f"pay_{i:04d}"
        cause = rnd.choices(causes, weights=weights)[0]
        method = rnd.choice(METHOD_BY_CAUSE[cause])
        failed_at = t0 + timedelta(minutes=rnd.randint(0, 60 * 24 * 3))

        # Amounts: mostly small, with a long tail of big-ticket orders.
        amount = int(rnd.choice([
            rnd.randint(199, 1500),
            rnd.randint(199, 1500),
            rnd.randint(1500, 6000),
            rnd.randint(6000, 40000),
        ]) * 100)

        has_mandate = method == "emandate" or (
            method == "card" and cause in ("insufficient_funds", "network_timeout")
            and rnd.random() < 0.5
        )

        t = Truth(payment_id=pid, cause=cause)

        if cause == "issuer_downtime":
            t.outage_ends_at = (failed_at + timedelta(minutes=rnd.randint(45, 240))).isoformat()
            t.nudge_conversion = rnd.uniform(0.25, 0.45)
        elif cause == "insufficient_funds":
            # Salary-day logic: funds land somewhere in the next 1-6 days.
            t.funds_arrive_at = (failed_at + timedelta(hours=rnd.randint(20, 140))).isoformat()
        elif cause == "checkout_abandon":
            t.nudge_conversion = rnd.uniform(0.35, 0.60)
        elif cause == "network_timeout":
            t.outage_ends_at = (failed_at + timedelta(minutes=rnd.randint(2, 20))).isoformat()
            t.nudge_conversion = rnd.uniform(0.25, 0.45)
        elif cause == "expired_instrument":
            t.nudge_conversion = rnd.uniform(0.15, 0.30)   # must update the card
        elif cause == "mandate_expired":
            t.nudge_conversion = rnd.uniform(0.20, 0.40)
        elif cause == "hard_decline":
            t.recoverable = False

        # Loyal customers often just come back and pay on their own.
        # Messaging them is money wasted and goodwill burned — and their
        # payment history is a signal the agent can legitimately observe.
        prior = rnd.choices([0, 1, 2, 4, 7], weights=[.34, .18, .16, .18, .14])[0]
        self_recovery_odds = 0.05 if prior < 3 else 0.42
        if t.recoverable and rnd.random() < self_recovery_odds:
            t.self_recovers_at = (failed_at + timedelta(hours=rnd.randint(3, 40))).isoformat()

        payments.append(Payment(
            id=pid,
            amount_paise=amount,
            method=method,
            has_mandate=has_mandate,
            failed_at=failed_at.isoformat(),
            raw_error=rnd.choice(RAW_ERRORS[cause]),
            customer_hint=(
                f"cust 9{rnd.randint(100000000, 999999999)} "
                f"card 4{rnd.randint(100000000000000, 999999999999999)}"
            ),
            prior_success_count=prior,
        ))
        truths[pid] = t

    return payments, truths


class Oracle:
    """
    The secret answer key, wrapped in an API.

    The agent may only ask: "I did ACTION on PAYMENT at TIME — what happened?"
    It never sees the cause or the timings. Answers are deterministic, so the
    baseline and Reflow are graded on exactly the same world.
    """

    def __init__(self, truths):
        self.truths = truths
        self.nudged = set()   # payment ids that have already received a link

    def attempt(self, payment: Payment, action: str, at: datetime) -> bool:
        t = self.truths[payment.id]

        if not t.recoverable:
            return False   # hard declines never succeed, by any route

        if action == "charge":
            # Charging only works if the merchant holds permission.
            if not payment.has_mandate:
                return False
            if t.cause in ("issuer_downtime", "network_timeout"):
                ok_after = datetime.fromisoformat(t.outage_ends_at)
                if at <= ok_after:
                    return False
                return _rng(payment.id, "charge", at.date(), at.hour).random() < 0.90
            if t.cause == "insufficient_funds":
                ok_after = datetime.fromisoformat(t.funds_arrive_at)
                if at <= ok_after:
                    return False
                return _rng(payment.id, "charge", at.date(), at.hour).random() < 0.85
            # checkout_abandon / expired / mandate_expired: charging is futile
            return False

        if action == "nudge":
            # A payment link only converts once, and only for causes where the
            # customer is the blocker.
            if payment.id in self.nudged:
                return False
            self.nudged.add(payment.id)
            if t.cause in ("checkout_abandon", "expired_instrument", "mandate_expired"):
                return _rng(payment.id, "nudge").random() < t.nudge_conversion
            if t.cause == "insufficient_funds":
                ok_after = datetime.fromisoformat(t.funds_arrive_at)
                if at > ok_after:
                    return _rng(payment.id, "nudge_if").random() < 0.45
                return False
            if t.cause in ("issuer_downtime", "network_timeout"):
                ok_after = datetime.fromisoformat(t.outage_ends_at)
                if at > ok_after:
                    return _rng(payment.id, "nudge_out").random() < t.nudge_conversion
            return False

        return False

    def self_recovered_by(self, payment: Payment, at: datetime) -> bool:
        """Did the customer come back and pay unaided before this moment?"""
        ts = self.truths[payment.id].self_recovers_at
        return ts is not None and datetime.fromisoformat(ts) <= at

    def would_self_recover(self, payment: Payment) -> bool:
        return self.truths[payment.id].self_recovers_at is not None

    def cause_of(self, payment_id: str) -> str:
        """Only used for grading diagnosis accuracy, never by the agent."""
        return self.truths[payment_id].cause


def cohort_to_json(payments):
    return [asdict(p) for p in payments]
