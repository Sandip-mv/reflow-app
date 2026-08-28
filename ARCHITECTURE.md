# Architecture

## Pipeline

```
   failed payment (messy gateway error string, amount, method, mandate flag)
            │
            ▼
   ┌──────────────────┐   redact PII → call model → parse JSON → validate cause
   │  diagnose.py     │   on timeout / bad JSON / unknown label → rules fallback
   └────────┬─────────┘   out: {cause, confidence, source}
            │
            ▼
   ┌──────────────────┐   plain table: cause → ordered steps
   │  policy.py       │   drops charge steps when no mandate is held
   └────────┬─────────┘   inserts pre-debit notice ahead of mandate debits
            │             holds messaging back for proven repeat payers
            ▼
   ┌──────────────────┐   8 rules. hard rules stop the step.
   │  guardrails.py   │   timing rules return the next lawful moment instead.
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐   idempotency key → execute → audit line
   │  executor.py     │   unresolved → dead-letter queue
   └────────┬─────────┘
            │
            ▼
   ┌──────────────────┐   same cohort, two policies, delta in rupees
   │  evaluate.py     │   + held-out diagnosis accuracy, p95 latency
   └────────┬─────────┘
            │
            ▼
      out/report.html
```

## Decision record

**Why the LLM classifies but does not decide.** Gateway error text is unstructured
and varies by acquirer, which suits a language model. Debiting an account does
not: the output is non-deterministic, hard to audit, and a bad sample double-charges
a real person. The boundary is enforced structurally — `policy.py` imports nothing
from `diagnose.py` except a cause string and a float.

**Why timing rules defer instead of block.** The first version blocked night-time
messages outright and silently lost recoveries. Compliance means acting lawfully,
not acting less. Deferral preserves the recovery and leaves a record of the delay.

**Why the baseline runs without guardrails.** The incumbent was never built to
respect these rules. Imposing them would manufacture a win.

**Why a seeded oracle.** Both policies must receive identical answers to identical
questions regardless of call order, or the comparison measures scheduling luck
rather than policy quality.

**Why a confidence floor.** Below 0.40 the correct action is to escalate, not to
guess. Payments that route here are counted separately from the 284 that
were processed correctly but not recovered — conflating the two would hide
real processing failures behind ordinary business outcomes.

## Data the agent sees vs. data it does not

| Visible to the agent | Hidden in the oracle |
|---|---|
| raw gateway error string | true failure cause |
| amount, method, mandate flag | when the bank outage ends |
| customer's prior payment count | when the customer's funds arrive |
| its own audit history | whether a payment link would convert |
| | whether the customer would have paid unaided |

## Failure modes and responses

| Failure | Response |
|---|---|
| model unreachable or slow | keyword fallback, flagged low confidence |
| model returns prose or fenced JSON | recovered by parser; unparseable → fallback |
| model returns an unknown cause label | rejected, treated as unparseable |
| confidence below floor | no action, routed to human review |
| process crash mid-plan | idempotency key prevents repeat execution |
| action unlawful at the planned time | rescheduled to the next lawful moment |
| action never lawful | blocked, reason written to audit trail |
| everything on fire | global kill switch, asserted by test |
