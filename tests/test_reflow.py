"""
Tests. Each compliance rule has one, because a rule you cannot run is a rule
you cannot prove.

Run with:  python3 -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime, timedelta

from reflow import guardrails, policy
from reflow.diagnose import redact, contains_pii, classify_by_rules, _parse, diagnose
from reflow.executor import Executor, baseline_steps
from reflow.guardrails import State
from reflow.policy import Step
from reflow.simulator import Oracle, make_cohort


def a_payment(**over):
    payments, _ = make_cohort(n=1, seed=1)
    p = payments[0]
    for k, v in over.items():
        setattr(p, k, v)
    return p


NOON = datetime(2026, 9, 2, 12, 0)


class TestRedaction(unittest.TestCase):
    def test_card_and_phone_are_masked(self):
        raw = "decline for cust 9876543210 card 4111111111111111"
        clean = redact(raw)
        self.assertNotIn("9876543210", clean)
        self.assertNotIn("4111111111111111", clean)
        self.assertFalse(contains_pii(clean))

    def test_email_and_vpa_are_masked(self):
        clean = redact("payer ravi.k@example.com vpa ravi@okhdfcbank failed")
        self.assertNotIn("ravi.k@example.com", clean)
        self.assertIn("REDACTED", clean)

    def test_nothing_leaves_undiagnosed(self):
        out = diagnose("DECLINED code=51 INSUFFICIENT FUNDS cust 9876543210")
        self.assertFalse(contains_pii(out["redacted_input"]))


class TestGuardrails(unittest.TestCase):
    def test_quiet_hours_block_messaging_at_night(self):
        step = Step("nudge_sms", 1.0, "x")
        v = guardrails.check(step, State(), NOON.replace(hour=2), a_payment())
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "quiet_hours")

    def test_quiet_hours_do_not_block_charges(self):
        step = Step("charge", 1.0, "x")
        p = a_payment(has_mandate=True, method="card")
        v = guardrails.check(step, State(), NOON.replace(hour=2), p)
        self.assertTrue(v.allowed)

    def test_attempt_cap(self):
        v = guardrails.check(Step("nudge_sms", 1.0, "x"),
                             State(attempts=guardrails.MAX_ATTEMPTS), NOON, a_payment())
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "attempt_cap")

    def test_charge_cool_off(self):
        state = State(last_charge_at=NOON - timedelta(minutes=20))
        v = guardrails.check(Step("charge", 1.0, "x"), state, NOON,
                             a_payment(has_mandate=True, method="card"))
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "cool_off")

    def test_hard_decline_blocks_further_charges(self):
        v = guardrails.check(Step("charge", 1.0, "x"), State(hard_declined=True),
                             NOON, a_payment(has_mandate=True, method="card"))
        self.assertFalse(v.allowed)

    def test_mandate_debit_requires_prior_notice(self):
        p = a_payment(method="emandate", has_mandate=True)
        v = guardrails.check(Step("charge", 1.0, "x"), State(), NOON, p)
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "pre_debit_notice")

    def test_notice_must_be_old_enough(self):
        p = a_payment(method="emandate", has_mandate=True)
        state = State(notice_sent_at=NOON - timedelta(hours=2))
        self.assertFalse(guardrails.check(Step("charge", 1.0, "x"), state, NOON, p).allowed)
        state = State(notice_sent_at=NOON - timedelta(hours=30))
        self.assertTrue(guardrails.check(Step("charge", 1.0, "x"), state, NOON, p).allowed)

    def test_spend_cap(self):
        state = State(spend=guardrails.MAX_SPEND_PER_PAYMENT)
        v = guardrails.check(Step("nudge_sms", 1.0, "x"), state, NOON, a_payment())
        self.assertFalse(v.allowed)

    def test_never_spend_more_than_the_payment_is_worth(self):
        tiny = a_payment(amount_paise=50)   # Rs 0.50
        v = guardrails.check(Step("nudge_whatsapp", 1.0, "x"), State(), NOON, tiny)
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "economic_sanity")


class TestPolicy(unittest.TestCase):
    def test_permanent_decline_produces_no_plan(self):
        steps, reason = policy.plan_for("hard_decline", 0.99, True, "card")
        self.assertEqual(steps, [])
        self.assertIn("prohibited", reason)

    def test_low_confidence_refuses_to_act(self):
        steps, reason = policy.plan_for("insufficient_funds", 0.1, True, "card")
        self.assertEqual(steps, [])
        self.assertIn("confidence", reason)

    def test_no_mandate_means_no_charge_steps(self):
        steps, _ = policy.plan_for("insufficient_funds", 0.9, False, "upi")
        self.assertTrue(all(s.kind != "charge" for s in steps))

    def test_mandate_debit_gets_a_notice_scheduled_first(self):
        steps, _ = policy.plan_for("insufficient_funds", 0.9, True, "emandate")
        self.assertEqual(steps[0].kind, "nudge")
        gap = min(s.delay_hours for s in steps if s.kind == "charge") - steps[0].delay_hours
        self.assertGreaterEqual(gap, guardrails.PRE_DEBIT_NOTICE_HOURS)

    def test_repeat_payers_are_not_messaged_immediately(self):
        steps, _ = policy.plan_for("checkout_abandon", 0.9, False, "card",
                                   prior_success_count=6)
        self.assertGreaterEqual(min(s.delay_hours for s in steps),
                                policy.LOYAL_GRACE_HOURS)


class TestDegradation(unittest.TestCase):
    def test_unparseable_model_output_returns_none(self):
        self.assertIsNone(_parse("I'm sorry, I can't do that"))
        self.assertIsNone(_parse(""))

    def test_fenced_json_is_recovered(self):
        got = _parse('```json\n{"cause":"hard_decline","confidence":0.9}\n```')
        self.assertEqual(got[0], "hard_decline")

    def test_invalid_cause_is_rejected(self):
        self.assertIsNone(_parse('{"cause":"banana","confidence":0.9}'))

    def test_fallback_classifier_still_works_offline(self):
        cause, conf = classify_by_rules("DECLINED code=54 EXPIRED CARD")
        self.assertEqual(cause, "expired_instrument")
        self.assertGreater(conf, 0)

    def test_diagnosis_never_raises(self):
        for junk in ["", "???", "\x00\x01", "a" * 5000]:
            self.assertIn("cause", diagnose(junk))


class TestExecutor(unittest.TestCase):
    def test_idempotency_prevents_repeat_actions(self):
        payments, truths = make_cohort(n=1, seed=3)
        p = payments[0]
        ex = Executor(Oracle(truths))
        steps = [Step("nudge_sms", 5.0, "x"), Step("nudge_sms", 5.0, "x")]
        ex.run(p, steps)
        skipped = [a for a in ex.audit if a["outcome"] == "skipped"
                   and "idempotency" in a["detail"]]
        self.assertEqual(len(skipped), 1)

    def test_every_decision_is_audited(self):
        payments, truths = make_cohort(n=20, seed=5)
        ex = Executor(Oracle(truths))
        for p in payments:
            ex.run(p, baseline_steps(p))
        self.assertTrue(ex.audit)
        for line in ex.audit:
            self.assertIn(line["outcome"],
                          {"executed", "blocked", "skipped", "recovered",
                           "deferred", "self_recovered"})
            self.assertTrue(line["detail"])

    def test_night_time_nudge_is_retimed_not_dropped(self):
        payments, truths = make_cohort(n=1, seed=9)
        p = payments[0]
        p.failed_at = datetime(2026, 9, 2, 23, 0).isoformat()
        ex = Executor(Oracle(truths))
        ex.run(p, [Step("nudge_sms", 1.0, "x")])
        deferred = [a for a in ex.audit if a["outcome"] == "deferred"]
        self.assertTrue(deferred)
        fired = [a for a in ex.audit if a["outcome"] in ("executed", "recovered")]
        self.assertTrue(fired)
        self.assertTrue(9 <= datetime.fromisoformat(fired[0]["at"]).hour < 21)

    def test_kill_switch_halts_everything(self):
        payments, truths = make_cohort(n=5, seed=2)
        guardrails.GLOBAL_KILL_SWITCH = True
        try:
            ex = Executor(Oracle(truths))
            for p in payments:
                ex.run(p, [Step("nudge_sms", 5.0, "x")])
            self.assertTrue(all(a["outcome"] != "executed" for a in ex.audit))
        finally:
            guardrails.GLOBAL_KILL_SWITCH = False


if __name__ == "__main__":
    unittest.main()
