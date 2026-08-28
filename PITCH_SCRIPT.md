# 5-minute pitch video — script and shot list

Total: 4:55. Read it in your own voice; don't memorise it word for word. Speaking slightly slower than feels natural is right on camera.

**Before you record**

```bash
python3 run.py > /dev/null && open out/report.html   # have the report ready in a tab
python3 -m unittest discover -s tests                # have a terminal ready
```

Record in 1080p. Screen share plus a small webcam corner if your tool supports it. One take per section is fine — cut them together.

---

## 0:00–0:45 — The problem

*Shot: you on camera, or a single slide with the four failure reasons.*

> A payment fails on an Indian checkout. Today the standard response is to retry the charge three times on a fixed schedule and then message everybody.
>
> That response ignores the only thing that matters — why it failed.
>
> If the issuing bank was down for two hours, all three retries land inside the outage. If the customer had no balance, retrying inside the hour is theatre; retrying near their salary date works. If they abandoned the OTP screen, the merchant holds no mandate at all, so every retry is *guaranteed* to fail — they needed a payment link, not a charge. And if the card was reported stolen, every retry costs a scheme fee and burns issuer trust.
>
> Four different problems. One treatment. I built Reflow to treat them differently.

---

## 0:45–2:15 — Live demo

*Shot: terminal, then the HTML report.*

Run it live:

```bash
python3 run.py
```

> No dependencies, no API key needed, one command. It's generating five hundred failed payments worth thirty-four lakh, then running two policies over the identical cohort.

When the table prints:

> Baseline recovers twelve point seven lakh. Reflow recovers fourteen point four six — thirteen and a half percent more, with most of those diagnoses — four hundred seventy-nine out of five hundred — coming from a real model running locally, not a rules fallback. Look at the row underneath: the baseline made fourteen hundred and thirty-two charge attempts. Reflow made a hundred and twelve. More money, ninety-two percent fewer attempts against customer instruments.

Switch to `out/report.html`, scroll to **Decision trace**:

> Every money decision is logged with its reason. This payment was recovered — but notice the amber line: the message was due at 11 PM, the quiet-hours rule caught it, and it was rescheduled to 9 AM rather than dropped.
>
> This one is a stolen card. Reflow chose to do nothing, and recorded why. Deciding not to act is a decision, and it gets audited like any other.
>
> And this customer simply came back and paid on their own. Reflow stood down instead of spending money to message someone who had already converted.

---

## 2:15–3:15 — Architecture and the AI boundary

*Shot: the pipeline diagram, or the four files open in your editor.*

> Four stages. Diagnose, decide, execute, measure.
>
> The model does exactly one job: it reads the messy gateway error string and returns a structured cause with a confidence. Bank error text is inconsistent, unstructured natural language that varies by acquirer — that's what language models are genuinely good at. PII is redacted before anything leaves the process; card numbers and phone numbers never reach a third-party API.
>
> The model does **not** decide whether to move money. That's this file — a plain readable table mapping cause to plan. I made that choice deliberately: an LLM's output is non-deterministic and hard to audit, and a bad sample means a real person gets charged twice. So the classifier classifies, and auditable code decides.
>
> Then eight compliance rules, each with a test. Attempt caps, cool-off, quiet hours, pre-debit notification for recurring mandates, a spend ceiling, and a kill switch.
>
> One thing I got wrong first time: my rules *blocked* night-time messages outright, which silently threw away recoveries in the name of compliance. That's a bug, not compliance. Now timing rules reschedule to the next lawful moment and log the deferral. That single fix took Reflow from losing to the baseline to beating it.

---

## 3:15–4:00 — Measurement

*Shot: report, "Same world, two policies" and "Does it hold in other worlds".*

> I can't test recovery on real money, so I built a world where the counterfactual has an answer. The simulator invents each failed payment *and* the hidden truth behind it — when the outage ends, when the salary lands, whether a link would convert. The agent never sees any of that. It only sees the error string.
>
> The oracle is seeded per payment, action and hour, so both policies get identical answers regardless of who asks first. And the baseline runs with guardrails **disabled** — holding the incumbent to rules it was never designed to respect would give me a flattering number instead of a true one.
>
> The lift holds across five independently generated worlds: thirteen and a half up to twenty-nine percent, mean twenty point eight.

And here's what it couldn't do. Two hundred fifty-one payments exhausted their retry plan without recovering — genuinely unrecoverable in this simulated world, not a system failure. Thirty-eight permanent declines were deliberately left alone rather than retried. Zero payments hit the dead-letter queue, meaning diagnosis always produced something usable. Accuracy on held-out errors is eighty-nine percent.
---

## 4:00–4:35 — Break it on purpose

*Shot: terminal.*

```bash
python3 run.py --break-llm
```

> I've just pointed the model at a dead endpoint. The pipeline doesn't crash — it falls back to a deterministic keyword classifier, flags the results low-confidence, and keeps recovering money. Anything below the confidence floor goes to human review instead of being guessed at.

```bash
python3 run.py --kill-switch
```

> And the kill switch: zero outbound actions executed. There's a test asserting that.

```bash
python3 -m unittest discover -s tests
```

> Twenty-six tests. Every compliance rule has one, because a rule you can't run is a rule you can't prove.

---

## 4:35–4:55 — Close

*Shot: you on camera.*

> Reflow recovers more money with a fraction of the attempts, and every rupee it moves is explainable, bounded, and logged.
>
> The world is synthetic and I've said so in the README — the conversion rates would change on real traffic, but the mechanism transfers. The next step is running the same policy against Razorpay test-mode Orders and Payments with forced-failure test cards, which is a swap of the executor's back end, not a rewrite.
>
> Repo's in the description. Thanks for watching.

---

## Notes

- Don't apologise for the synthetic data. State it once, calmly, and move on. Volunteering the limitation reads as confidence; getting caught on it reads badly.
- If you go long, cut the architecture section to 45s. Never cut the metrics or the break-it demo — those are what separate the submission.
- Have `out/report.html` already open in a tab. Do not generate it live and then wait.
- If asked in the panel *"why not let the LLM decide the retry?"* — that's the question they want you to have an answer to. Non-determinism, auditability, and double-charge risk. Say it in one breath.
