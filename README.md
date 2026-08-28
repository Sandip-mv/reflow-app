# Reflow

**A bounded payment-failure recovery agent.** When a payment fails, Reflow works out why, chooses the cheapest intervention that could plausibly work, executes it inside hard compliance limits, and reports the rupees recovered against the method merchants run today.

Submitted to the Razorpay AI Buildathon 2026 — Track 3, AI Revenue Recovery.

---

## The problem

A payment fails. The merchant loses the sale. The industry-standard response is to retry the charge three times on a fixed schedule and then send everyone a reminder.

That response ignores the only thing that matters: **why** it failed.

- The issuing bank was down for two hours → all three retries land inside the outage and fail.
- The customer had no money in the account → retrying within the hour is theatre; retrying near their next inflow works.
- The customer abandoned the OTP screen → the merchant holds no mandate, so *every* retry is guaranteed to fail. They needed a payment link, not a charge.
- The card was reported stolen → each retry costs a scheme fee and erodes issuer trust.

Same treatment, four different problems. Reflow treats them differently.

## Results

500 failed payments, ₹34,15,598 at risk. Both policies run against the identical cohort in the identical simulated world.

| | Fixed-retry baseline | Reflow |
|---|---:|---:|
| Payments recovered | 181 | **211** |
| Value recovered | ₹12,74,464 | **₹14,46,282** |
| Charge attempts on customer instruments | 1,432 | **112** |
| Messaging spend | ₹110 | ₹150 |
| Payments not recovered | 319 | 289 |

**+13.5% more value recovered using 92% fewer charge attempts, with 479 of 500 diagnoses made by the actual local model** (`source={'llm': 479, 'fallback': 21}`, 89% diagnosis accuracy on held-out errors).

The lift holds across five independently generated worlds: +13.5%, +19.3%, +22.6%, +29.0%, +19.5% (mean **+20.8%**). One favourable random draw is not a result.

The 92% reduction in charge attempts matters as much as the money. Every retry against a customer's instrument carries a scheme fee and a small cost in issuer trust. The baseline buys its recoveries by hammering instruments it has no permission to charge.

## Run it

Python 3.10+. No dependencies, no network, no API key required.

```bash
git clone <this repo> && cd reflow
python3 run.py
```

Writes `out/report.html` — open it in any browser.

```bash
python3 run.py --n 1000        # larger cohort
python3 run.py --break-llm     # sabotage the model, show graceful degradation
python3 run.py --kill-switch   # engage the global halt, prove zero actions fire
python3 -m unittest discover -s tests -v    # 26 tests
```

Diagnosis runs against a local model by default — [Ollama](https://ollama.com), model `qwen2.5:3b`, no API key and no network call ever required:

```bash
ollama serve                   # in a separate terminal, if not already running
ollama pull qwen2.5:3b
python3 run.py
```

`--break-llm` points the client at a dead port to prove the fallback classifier engages cleanly. The report states which mode produced its numbers, and how many diagnoses came from each source.

## How it works

```
failed payment
   ↓
diagnose.py     LLM reads the messy gateway error → structured cause + confidence
   ↓            (PII redacted first; falls back to rules if the model fails)
policy.py       cause → ordered plan of at most 3 steps        ← plain code, not a model
   ↓
guardrails.py   8 rules. Hard rules stop the action.
   ↓            Timing rules reschedule it to the next lawful moment.
executor.py     idempotent execution, full audit trail, dead-letter queue
   ↓
evaluate.py     same cohort, two policies, measured delta in rupees
```

### Where the LLM is used, and where it is not

The model classifies text. It does not decide whether to move money.

Gateway error strings are inconsistent, unstructured natural language that varies by bank and acquirer — exactly what language models are good at. Deciding whether to debit a customer's account is exactly what they should not be trusted with: the output is non-deterministic, hard to audit, and a bad sample means a real person is charged twice.

So `policy.py` is a readable table. You can diff it, test it, and hand it to a compliance reviewer.

### Guardrails, as code

Eight rules in `guardrails.py`, each with a test:

| Rule | Effect |
|---|---|
| `attempt_cap` | 3 actions per payment, of which at most 2 charges |
| `cool_off` | minimum 2h between charge attempts |
| `quiet_hours` | messaging only 09:00–21:00 |
| `pre_debit_notice` | recurring mandates require 24h advance notice before debit |
| `spend_cap` | ₹1.00 maximum recovery spend per payment |
| `economic_sanity` | never spend more than 25% of the amount at risk |
| `hard_decline_block` | permanent issuer declines are never retried |
| `kill_switch` | one flag halts all outbound actions |

**Timing rules defer rather than discard.** An early version blocked night-time messages outright, and silently threw away recoveries in the name of compliance. That is a bug, not compliance. Blocked-but-lawful actions are now rescheduled to the next permitted window, and the deferral is written to the audit trail. This single fix moved Reflow from losing to the baseline to beating it.

### Failure handling

- **Idempotency keys** — every action is keyed `{policy}:{payment}:{action}:{offset}`. A crash and restart cannot double-charge.
- **Model degradation** — timeout, network error, fenced JSON, prose instead of JSON, or a hallucinated cause label all fall back to a deterministic keyword classifier, flagged as low confidence.
- **Confidence floor** — below 0.40, Reflow refuses to act and routes the payment to human review rather than guessing with someone's money.
- **Dead-letter queue** — payments the system cannot process (unreadable diagnosis, unhandled error) go to human review instead of being retried blindly. Kept strictly separate from the 284 payments that were processed correctly but simply could not be recovered.
- **Kill switch** — verified by test: zero outbound actions when engaged.

## Evaluation method

You cannot test recovery strategies on real money, so the project builds a world where the counterfactual has a defined answer.

`simulator.py` invents each failed payment *and* the hidden truth behind it — when the bank outage ends, when the customer's salary lands, whether a payment link would convert, whether the customer was going to come back and pay unaided anyway. The agent never sees any of this. It only sees the messy error string.

The oracle then answers one question: *"you did X at time T — did it work?"* Answers are seeded per payment, action and hour, so they are identical no matter which policy asks or in what order. That is what makes the comparison fair.

The baseline runs with **guardrails disabled**. Holding the incumbent to rules it was never designed to respect would produce a flattering number rather than a true one.

## What this does not prove

Stated plainly, because a result you have to caveat later is worth less than one you caveat now.

- **The world is synthetic.** Conversion rates, outage lengths and salary timing are modelled from plausible assumptions, not from Razorpay's data. The *mechanism* is what transfers; the exact percentages would change on real traffic.
- **Diagnosis accuracy is measured against generated error strings.** Real gateway output is messier and more varied.
- **Offline mode uses the keyword classifier**, which is easier than the real task. Runs with `ANTHROPIC_API_KEY` set exercise the actual model path; the report labels which one produced its numbers.
- **No live money movement.** The Razorpay integration surface is modelled, not called. The next step is running the same policy against test-mode Orders and Payments with forced-failure test cards.
- **Second-order effects are not modelled** — customer churn from over-messaging, issuer penalties for excessive retries. Both would widen the gap in Reflow's favour, so leaving them out is the conservative choice.

## Layout

```
run.py                  one command, end to end
reflow/simulator.py     the world and its hidden answer key
reflow/diagnose.py      redaction, LLM call, structured parse, rules fallback
reflow/policy.py        cause → plan. plain, auditable, no model
reflow/guardrails.py    8 compliance rules, deferral-aware
reflow/executor.py      idempotency, audit trail, dead-letter queue
reflow/evaluate.py      the scoreboard
reflow/report.py        self-contained HTML console
tests/test_reflow.py    26 tests
```
