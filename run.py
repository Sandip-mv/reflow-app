#!/usr/bin/env python3
"""
Reflow — one command, end to end.
    python3 run.py                 # 500 payments, prints results, writes report
    python3 run.py --n 1000        # bigger cohort
    python3 run.py --break-llm     # simulate the model being unreachable
    python3 run.py --kill-switch   # prove nothing goes out when halted
No dependencies. Python 3.10+ and nothing else.
"""
import argparse
import os
from reflow import guardrails
from reflow.evaluate import full_run
from reflow.report import build

BAR = "─" * 72


def rs(x):
    return f"Rs {x:,.0f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--seeds", type=int, nargs="*", default=[7, 11, 23, 42, 99])
    ap.add_argument("--break-llm", action="store_true",
                    help="point the model at a dead endpoint to prove graceful fallback")
    ap.add_argument("--kill-switch", action="store_true",
                    help="engage the global halt and show that nothing executes")
    args = ap.parse_args()

    if args.break_llm:
        os.environ["REFLOW_BREAK_LLM"] = "1"
        print("!! model endpoint sabotaged — expecting graceful degradation\n")
    if args.kill_switch:
        guardrails.GLOBAL_KILL_SWITCH = True
        print("!! global kill switch engaged — expecting zero outbound actions\n")

    run = full_run(n=args.n, seed=args.seed)
    b, f = run["baseline"], run["reflow"]
    lift = (f["recovered_rupees"] - b["recovered_rupees"]) / max(b["recovered_rupees"], 1)

    print(BAR)
    print(f"COHORT   {args.n} failed payments   {rs(b['at_risk'])} at risk")
    print(BAR)
    print(f"{'':34}{'baseline':>16}{'reflow':>16}")
    print(f"{'payments recovered':34}{b['recovered_count']:>16}{f['recovered_count']:>16}")
    print(f"{'value recovered':34}{rs(b['recovered_rupees']):>16}{rs(f['recovered_rupees']):>16}")
    print(f"{'charge attempts':34}{b['attempts'] - b['nudges']:>16}{f['attempts'] - f['nudges']:>16}")
    print(f"{'messages sent':34}{b['nudges']:>16}{f['nudges']:>16}")
    print(f"{'messaging spend':34}{rs(b['cost']):>16}{rs(f['cost']):>16}")
    print(f"{'messages wasted on self-payers':34}{b['false_nudges']:>16}{f['false_nudges']:>16}")
    print(BAR)
    print(f"LIFT     {lift:+.1%}   ({rs(f['recovered_rupees'] - b['recovered_rupees'])} more recovered)")
    print(f"DIAGNOSIS {run['diagnosis']['accuracy']:.1%} on {run['diagnosis']['holdout_size']} "
          f"held-out errors   source={run['diagnosis']['sources']}   "
          f"p95={run['latency']['p95_ms']:.1f}ms")
    print(f"DEAD LETTER {len(f['dead_letter'])} payments routed to human review")
    print(BAR)

    if args.kill_switch:
        executed = [a for a in f["audit"] if a["outcome"] in ("executed", "recovered")]
        print(f"kill switch check: {len(executed)} outbound actions executed "
              f"(expected 0)\n")
        return

    print("\nexceptions (unresolved, stated honestly):")
    for reason, n in f["exceptions"]:
        print(f"  {n:>4}  {reason}")

    seeds = []
    for s in args.seeds:
        r = full_run(n=args.n, seed=s)
        seeds.append((s, r["baseline"]["recovered_rupees"], r["reflow"]["recovered_rupees"]))

    print("\nrobustness across independently generated worlds:")
    for s, bb, rr in seeds:
        print(f"  seed {s:>3}   baseline {rs(bb):>12}   reflow {rs(rr):>12}   "
              f"{(rr - bb) / max(bb, 1):+.1%}")

    path = build(run, seeds)
    print(f"\nreport written to {path}")


if __name__ == "__main__":
    main()
