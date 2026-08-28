"""
policy.py — the brain, and deliberately NOT an LLM.

Given a diagnosed cause, produce an ordered plan of at most a few steps.
Each step says: what to do, and how long after the failure to do it.

This is plain data. You can read it, diff it, unit-test it, and hand it to a
compliance reviewer. A language model deciding whether to charge a customer's
card would be none of those things.

Cost model (rupees) is deliberate: retries are effectively free, messaging
is not. A recovery that costs more than it returns is not a recovery.
"""

from dataclasses import dataclass

ACTION_COST = {
    "charge": 0.0,
    "nudge_sms": 0.25,
    "nudge_whatsapp": 0.35,
}


@dataclass
class Step:
    action: str          # "charge" | "nudge_sms" | "nudge_whatsapp"
    delay_hours: float   # measured from the moment of failure
    note: str

    @property
    def cost(self) -> float:
        return ACTION_COST[self.action]

    @property
    def kind(self) -> str:
        return "charge" if self.action == "charge" else "nudge"


# The whole policy, in one readable table.
PLANS = {
    # Bank was down. Wait for it to come back, then try. Do not pester the
    # customer — the failure was not their doing.
    "issuer_downtime": [
        Step("charge", 4.0, "wait out the typical outage window"),
        Step("charge", 12.0, "second attempt after a longer cool-off"),
        Step("nudge_whatsapp", 14.0, "no mandate, or two charges spent — send a link"),
    ],

    # No money in the account. Retrying within the hour is theatre.
    # Aim for the next inflow, then ask.
    "insufficient_funds": [
        Step("charge", 30.0, "first payday-window attempt"),
        Step("charge", 78.0, "second inflow window"),
        Step("nudge_whatsapp", 96.0, "ask the customer to complete it"),
    ],

    # Customer walked away mid-authentication. We hold no permission to
    # charge, so retrying is guaranteed to fail. Send a fresh link, fast.
    "checkout_abandon": [
        Step("nudge_whatsapp", 1.0, "strike while intent is warm"),
        Step("nudge_sms", 20.0, "one reminder, then stop"),
    ],

    # Transient network blip. Cheap and quick to retry.
    "network_timeout": [
        Step("charge", 0.5, "transient — retry shortly"),
        Step("charge", 3.0, "second attempt"),
        Step("nudge_sms", 5.0, "fall back to a fresh payment link"),
    ],

    # Card is dead. No retry will ever succeed; only the customer can fix it.
    "expired_instrument": [
        Step("nudge_whatsapp", 2.0, "ask for an updated card"),
    ],

    # Mandate lapsed. Needs re-authorisation by the customer.
    "mandate_expired": [
        Step("nudge_whatsapp", 2.0, "ask to re-authorise the mandate"),
        Step("charge", 30.0, "attempt once re-auth window has passed"),
    ],

    # Permanent refusal. The correct action is to stop. Every retry here
    # costs the merchant a scheme fee and risks an issuer penalty.
    "hard_decline": [],

    # We could not tell. Do not guess with someone's money.
    "unknown": [],
}

# Below this confidence we refuse to act on the diagnosis and route the
# payment to human review instead.
CONFIDENCE_FLOOR = 0.40


LOYAL_CUSTOMER_THRESHOLD = 3      # prior successful payments
LOYAL_GRACE_HOURS = 30.0          # give them time to come back unaided


def plan_for(cause: str, confidence: float, has_mandate: bool, method: str = "",
             prior_success_count: int = 0):
    """
    Returns (steps, reason_if_empty).

    Two adjustments happen here, and both matter:

    - Charge steps are dropped when the merchant holds no mandate. No
      permission, no charge. This alone removes a large slice of the
      attempts the baseline wastes.
    - Recurring mandates get a pre-debit notification scheduled ahead of
      the first charge, because the guardrail will otherwise block it.
      Planning around a compliance rule beats colliding with it.
    """
    if cause not in PLANS:
        return [], "unrecognised cause"
    if confidence < CONFIDENCE_FLOOR:
        return [], f"diagnosis confidence {confidence:.2f} below floor"
    if cause == "hard_decline":
        return [], "permanent decline — retrying is prohibited"

    steps = list(PLANS[cause])

    if not has_mandate:
        steps = [s for s in steps if s.kind != "charge"]
        if not steps:
            return [], "no mandate on file and no customer-facing step available"

    if method == "emandate":
        charges = [s for s in steps if s.kind == "charge"]
        if charges and not any("pre-debit" in s.note for s in steps):
            first = min(c.delay_hours for c in charges)
            notice = Step("nudge_sms", max(0.5, first - 25.0),
                          "pre-debit notification required before mandate debit")
            steps = [notice] + steps

    # Customers with a payment history usually return on their own. Nudging
    # them early spends money and patience on a conversion that was coming
    # anyway, so messaging is held back — charges are unaffected.
    if prior_success_count >= LOYAL_CUSTOMER_THRESHOLD:
        steps = [
            Step(s.action, max(s.delay_hours, LOYAL_GRACE_HOURS),
                 s.note + " (held back: repeat payer)")
            if s.kind == "nudge" else s
            for s in steps
        ]

    steps.sort(key=lambda s: s.delay_hours)
    return steps, None
