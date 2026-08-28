"""
evaluate.py — the scoreboard.

Runs two policies over the SAME cohort, in the SAME world, and reports the
difference in rupees. Also grades the diagnosis layer on a held-out slice
that was never used while building the classifier.

Every number this file prints is measured, not asserted.
"""

import time
from collections import Counter

from .diagnose import diagnose, classify_by_rules
from .executor import Executor, baseline_steps
from .policy import plan_for
from .simulator import Oracle, make_cohort


def run_baseline(payments, truths):
    oracle = Oracle(truths)
    ex = Executor(oracle, enforce_guardrails=False)
    results = [ex.run(p, baseline_steps(p), label="baseline") for p in payments]
    return summarise("Fixed-retry baseline", payments, results, oracle, ex)


def run_reflow(payments, truths, diagnoses):
    oracle = Oracle(truths)
    ex = Executor(oracle, enforce_guardrails=True)
    results = []
    for p in payments:
        d = diagnoses[p.id]
        steps, reason = plan_for(d["cause"], d["confidence"], p.has_mandate,
                                 p.method, p.prior_success_count)
        results.append(ex.run(p, steps, plan_reason=reason, label="reflow"))
    return summarise("Reflow", payments, results, oracle, ex)


def summarise(name, payments, results, oracle, ex):
    by_id = {p.id: p in payments and p for p in payments}
    at_risk = sum(p.amount_rupees() for p in payments)
    recovered = [r for r in results if r["recovered"]]

    # A nudge sent to someone who would have come back on their own is
    # money spent and patience spent, for nothing. Most systems never
    # measure this. It is the cost of being annoying.
    false_nudges = sum(
        1 for r in results
        if r["nudges"] > 0 and oracle.would_self_recover(by_id[r["payment_id"]])
    )

    total_cost = sum(r["cost"] for r in results)
    total_recovered = sum(r["amount"] for r in results)

    exceptions = Counter(r["exception"] for r in results if r["exception"])
    unaided = sum(1 for r in results if r.get("unaided"))

    return {
        "name": name,
        "payments": len(payments),
        "at_risk": at_risk,
        "recovered_count": len(recovered),
        "recovered_rupees": total_recovered,
        "recovery_rate": len(recovered) / len(payments) if payments else 0.0,
        "value_rate": total_recovered / at_risk if at_risk else 0.0,
        "attempts": sum(r["attempts"] for r in results),
        "nudges": sum(r["nudges"] for r in results),
        "cost": total_cost,
        "cost_per_recovery": total_cost / len(recovered) if recovered else 0.0,
        "false_nudges": false_nudges,
        "unaided_recoveries": unaided,
        "agent_driven_recoveries": len(recovered) - unaided,
        "exceptions": exceptions.most_common(),
        "audit": ex.audit,
        "dead_letter": ex.dead_letter,
        "exhausted": ex.exhausted,
        "results": results,
    }


def grade_diagnosis(payments, truths, diagnoses, holdout_frac=0.2):
    """
    Accuracy on the last slice of the cohort, which the keyword rules were
    never tuned against. Reported separately from the money numbers because
    they measure different things.
    """
    cut = int(len(payments) * (1 - holdout_frac))
    holdout = payments[cut:]
    correct = sum(
        1 for p in holdout if diagnoses[p.id]["cause"] == truths[p.id].cause
    )
    confusion = Counter(
        (truths[p.id].cause, diagnoses[p.id]["cause"])
        for p in holdout if diagnoses[p.id]["cause"] != truths[p.id].cause
    )
    sources = Counter(diagnoses[p.id]["source"] for p in payments)
    return {
        "holdout_size": len(holdout),
        "correct": correct,
        "accuracy": correct / len(holdout) if holdout else 0.0,
        "top_confusions": confusion.most_common(5),
        "sources": dict(sources),
    }


def diagnose_all(payments):
    """Diagnose every payment once, and time it."""
    diagnoses, latencies = {}, []
    for p in payments:
        t0 = time.perf_counter()
        diagnoses[p.id] = diagnose(p.raw_error)
        latencies.append((time.perf_counter() - t0) * 1000)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    return diagnoses, {"p95_ms": p95, "mean_ms": sum(latencies) / len(latencies)}


def full_run(n=500, seed=7):
    payments, truths = make_cohort(n=n, seed=seed)
    diagnoses, latency = diagnose_all(payments)
    return {
        "payments": payments,
        "truths": truths,
        "diagnosis": grade_diagnosis(payments, truths, diagnoses),
        "latency": latency,
        "baseline": run_baseline(payments, truths),
        "reflow": run_reflow(payments, truths, diagnoses),
        "diagnoses": diagnoses,
    }
