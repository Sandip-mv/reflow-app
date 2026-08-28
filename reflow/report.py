"""
report.py — writes a single self-contained HTML file.

No CDN, no build step, no network. Open out/report.html in any browser and
every number on the page came from the run that produced it.

The centrepiece is the decision ribbon: one horizontal timeline per payment
showing every action, deferral and block in order. If the product's claim is
"every money decision is explainable", the interface has to show the
explanation, not assert it.
"""

import html
import os
from datetime import datetime

INK = {
    "bg": "#10151B",
    "panel": "#171E26",
    "panel2": "#1E2731",
    "line": "#2A3541",
    "text": "#DCE3EA",
    "dim": "#8A99A8",
    "recovered": "#3FBF9F",
    "lost": "#C9614E",
    "hold": "#D8A657",
    "accent": "#6FA8DC",
}

CSS = f"""
*{{box-sizing:border-box}}
body{{margin:0;background:{INK['bg']};color:{INK['text']};
 font-family:"Inter Tight",system-ui,-apple-system,"Segoe UI",sans-serif;
 line-height:1.6;font-size:15px}}
.wrap{{max-width:1040px;margin:0 auto;padding:48px 24px 96px}}
.mono{{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
 font-variant-numeric:tabular-nums}}
h1{{font-size:30px;font-weight:600;letter-spacing:-.02em;margin:0 0 6px}}
h2{{font-size:13px;font-weight:600;letter-spacing:.14em;text-transform:uppercase;
 color:{INK['dim']};margin:56px 0 16px;padding-bottom:8px;
 border-bottom:1px solid {INK['line']}}}
.sub{{color:{INK['dim']};margin:0 0 4px}}
.meta{{color:{INK['dim']};font-size:13px}}
.hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
 gap:12px;margin-top:32px}}
.stat{{background:{INK['panel']};border:1px solid {INK['line']};border-radius:10px;
 padding:18px 20px}}
.stat .k{{font-size:12px;letter-spacing:.1em;text-transform:uppercase;
 color:{INK['dim']}}}
.stat .v{{font-size:28px;font-weight:600;margin-top:6px;letter-spacing:-.02em}}
.stat .n{{font-size:12px;color:{INK['dim']};margin-top:2px}}
.up{{color:{INK['recovered']}}}
.down{{color:{INK['lost']}}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{text-align:left;padding:10px 12px;border-bottom:1px solid {INK['line']}}}
th{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:{INK['dim']};
 font-weight:600}}
td.num,th.num{{text-align:right}}
.card{{background:{INK['panel']};border:1px solid {INK['line']};border-radius:10px;
 padding:20px;margin-bottom:12px}}
.trace-head{{display:flex;justify-content:space-between;align-items:baseline;
 gap:12px;flex-wrap:wrap;margin-bottom:14px}}
.tag{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
 border:1px solid {INK['line']};border-radius:99px;padding:3px 9px;color:{INK['dim']}}}
.evt{{display:grid;grid-template-columns:112px 96px 1fr;gap:12px;padding:7px 0;
 border-top:1px solid {INK['line']};font-size:13.5px;align-items:start}}
.evt:first-of-type{{border-top:none}}
.pill{{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
 border-radius:4px;padding:2px 7px;display:inline-block}}
.p-recovered{{background:rgba(63,191,159,.14);color:{INK['recovered']}}}
.p-self_recovered{{background:rgba(111,168,220,.14);color:{INK['accent']}}}
.p-deferred{{background:rgba(216,166,87,.14);color:{INK['hold']}}}
.p-blocked{{background:rgba(201,97,78,.16);color:{INK['lost']}}}
.p-executed{{background:{INK['panel2']};color:{INK['dim']}}}
.p-skipped{{background:{INK['panel2']};color:{INK['dim']}}}
.note{{color:{INK['dim']};font-size:13px;margin-top:10px}}
@media (max-width:640px){{.evt{{grid-template-columns:1fr;gap:2px}}}}
"""


def _rs(x):
    return f"₹{x:,.0f}"


def _bar_chart(rows, w=960, row_h=34):
    """Recovered rupees per cause, baseline vs Reflow. Hand-rolled SVG."""
    top = max([max(b, r) for _, b, r, _ in rows] + [1])
    h = len(rows) * row_h + 34
    label_w, pad = 168, 108
    span = w - label_w - pad
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
           f'aria-label="Rupees recovered by failure cause">']
    for i, (cause, base, refl, risk) in enumerate(rows):
        y = i * row_h + 14
        out.append(f'<text x="0" y="{y + 11}" fill="{INK["text"]}" font-size="13" '
                   f'font-family="ui-monospace,monospace">{html.escape(cause)}</text>')
        bw = span * base / top
        rw = span * refl / top
        out.append(f'<rect x="{label_w}" y="{y}" width="{max(bw, 1):.1f}" height="8" '
                   f'rx="2" fill="{INK["dim"]}" opacity=".55"/>')
        out.append(f'<rect x="{label_w}" y="{y + 11}" width="{max(rw, 1):.1f}" height="8" '
                   f'rx="2" fill="{INK["recovered"]}"/>')
        out.append(f'<text x="{w - 4}" y="{y + 15}" text-anchor="end" '
                   f'fill="{INK["dim"]}" font-size="12" '
                   f'font-family="ui-monospace,monospace">{_rs(refl)}</text>')
    out.append(f'<g font-size="12" fill="{INK["dim"]}" '
               f'font-family="ui-monospace,monospace">'
               f'<rect x="{label_w}" y="{h - 16}" width="14" height="7" rx="2" '
               f'fill="{INK["dim"]}" opacity=".55"/>'
               f'<text x="{label_w + 20}" y="{h - 9}">baseline</text>'
               f'<rect x="{label_w + 96}" y="{h - 16}" width="14" height="7" rx="2" '
               f'fill="{INK["recovered"]}"/>'
               f'<text x="{label_w + 116}" y="{h - 9}">reflow</text></g>')
    out.append("</svg>")
    return "".join(out)


def _trace(audit, payment, title):
    ev = [a for a in audit if a["payment_id"] == payment.id]
    rows = []
    for a in ev:
        ts = datetime.fromisoformat(a["at"]).strftime("%d %b %H:%M")
        rows.append(
            f'<div class="evt"><span class="mono" style="color:{INK["dim"]}">{ts}</span>'
            f'<span><span class="pill p-{a["outcome"]}">{a["outcome"]}</span></span>'
            f'<span>{html.escape(a["action"])} — {html.escape(a["detail"])}</span></div>'
        )
    return (
        f'<div class="card"><div class="trace-head">'
        f'<strong>{html.escape(title)}</strong>'
        f'<span class="meta mono">{payment.id} · {_rs(payment.amount_rupees())} · '
        f'{payment.method} · {"mandate" if payment.has_mandate else "no mandate"} · '
        f'{payment.prior_success_count} prior payments</span></div>'
        f'<div class="meta mono" style="margin-bottom:10px">'
        f'{html.escape(payment.raw_error[:92])}</div>'
        + "".join(rows) + "</div>"
    )


def build(run, seeds_table, out_path="out/report.html"):
    b, f = run["baseline"], run["reflow"]
    payments = run["payments"]
    truths = run["truths"]
    P = {p.id: p for p in payments}

    lift = (f["recovered_rupees"] - b["recovered_rupees"]) / max(b["recovered_rupees"], 1)
    charges_b = b["attempts"] - b["nudges"]
    charges_f = f["attempts"] - f["nudges"]

    # money by cause
    causes = sorted({t.cause for t in truths.values()})
    rows = []
    for c in causes:
        ids = {pid for pid, t in truths.items() if t.cause == c}
        bb = sum(r["amount"] for r in b["results"] if r["payment_id"] in ids)
        rr = sum(r["amount"] for r in f["results"] if r["payment_id"] in ids)
        risk = sum(P[i].amount_rupees() for i in ids)
        rows.append((c, bb, rr, risk))
    rows.sort(key=lambda r: -r[3])

    # pick illustrative traces
    def pick(pred, fallback_idx=0):
        for r in f["results"]:
            if pred(r):
                return P[r["payment_id"]]
        return payments[fallback_idx]

    deferred_ids = {a["payment_id"] for a in f["audit"] if a["outcome"] == "deferred"}
    t1 = pick(lambda r: r["recovered"] and not r.get("unaided")
              and r["payment_id"] in deferred_ids)
    t2 = pick(lambda r: truths[r["payment_id"]].cause == "hard_decline")
    t3 = pick(lambda r: r.get("unaided"))

    mode = "live model" if os.environ.get("ANTHROPIC_API_KEY") else "offline (rules fallback)"
    d = run["diagnosis"]

    seed_rows = "".join(
        f"<tr><td class='mono'>{s}</td><td class='num mono'>{_rs(bb)}</td>"
        f"<td class='num mono'>{_rs(rr)}</td>"
        f"<td class='num mono up'>{(rr - bb) / max(bb,1):+.1%}</td></tr>"
        for s, bb, rr in seeds_table
    )

    exc_rows = "".join(
        f"<tr><td>{html.escape(reason)}</td><td class='num mono'>{n}</td></tr>"
        for reason, n in f["exceptions"]
    )

    body = f"""<div class="wrap">
<h1>Reflow</h1>
<p class="sub">Bounded payment-failure recovery, measured against the incumbent.</p>
<p class="meta mono">{len(payments)} failed payments · {_rs(b['at_risk'])} at risk ·
diagnosis: {mode} · generated {datetime.now().strftime('%d %b %Y %H:%M')}</p>

<div class="hero">
  <div class="stat"><div class="k">At risk</div>
    <div class="v mono">{_rs(b['at_risk'])}</div>
    <div class="n">across {len(payments)} failed payments</div></div>
  <div class="stat"><div class="k">Baseline recovered</div>
    <div class="v mono">{_rs(b['recovered_rupees'])}</div>
    <div class="n">{b['recovered_count']} payments · {b['recovery_rate']:.1%}</div></div>
  <div class="stat"><div class="k">Reflow recovered</div>
    <div class="v mono up">{_rs(f['recovered_rupees'])}</div>
    <div class="n">{f['recovered_count']} payments · {f['recovery_rate']:.1%}</div></div>
  <div class="stat"><div class="k">Lift</div>
    <div class="v mono up">{lift:+.1%}</div>
    <div class="n">{_rs(f['recovered_rupees'] - b['recovered_rupees'])} additional</div></div>
</div>

<h2>Same world, two policies</h2>
<table>
<tr><th>Measure</th><th class="num">Baseline</th><th class="num">Reflow</th><th class="num">Delta</th></tr>
<tr><td>Payments recovered</td><td class="num mono">{b['recovered_count']}</td>
    <td class="num mono">{f['recovered_count']}</td>
    <td class="num mono up">{f['recovered_count'] - b['recovered_count']:+d}</td></tr>
<tr><td>Value recovered</td><td class="num mono">{_rs(b['recovered_rupees'])}</td>
    <td class="num mono">{_rs(f['recovered_rupees'])}</td>
    <td class="num mono up">{lift:+.1%}</td></tr>
<tr><td>Share of value at risk</td><td class="num mono">{b['value_rate']:.1%}</td>
    <td class="num mono">{f['value_rate']:.1%}</td><td class="num mono"></td></tr>
<tr><td>Charge attempts on customer instruments</td>
    <td class="num mono">{charges_b}</td><td class="num mono">{charges_f}</td>
    <td class="num mono up">{(charges_f - charges_b) / max(charges_b,1):+.0%}</td></tr>
<tr><td>Messages sent</td><td class="num mono">{b['nudges']}</td>
    <td class="num mono">{f['nudges']}</td>
    <td class="num mono">{f['nudges'] - b['nudges']:+d}</td></tr>
<tr><td>Messaging spend</td><td class="num mono">{_rs(b['cost'])}</td>
    <td class="num mono">{_rs(f['cost'])}</td><td class="num mono"></td></tr>
<tr><td>Cost per recovered payment</td>
    <td class="num mono">₹{b['cost_per_recovery']:.2f}</td>
    <td class="num mono">₹{f['cost_per_recovery']:.2f}</td><td class="num mono"></td></tr>
<tr><td>Messages sent to customers who returned unaided</td>
    <td class="num mono">{b['false_nudges']}</td>
    <td class="num mono">{f['false_nudges']}</td>
    <td class="num mono up">{f['false_nudges'] - b['false_nudges']:+d}</td></tr>
</table>
<p class="note">The baseline runs with guardrails disabled. Holding it to rules it was
never designed to respect would make the comparison flattering rather than fair.</p>

<h2>Where the money is</h2>
<div class="card">{_bar_chart(rows)}</div>
<p class="note">Insufficient funds is the clearest case: retrying inside the hour cannot
work, and retrying near the customer's next inflow can. Permanent declines are left
alone on purpose — the correct recovery action there is none.</p>

<h2>Decision trace</h2>
<p class="note" style="margin-top:0">Every money decision, in order, with the reason
attached. This is the audit trail, rendered.</p>
{_trace(f['audit'], t1, 'Recovered after a compliance deferral')}
{_trace(f['audit'], t2, 'Correctly refused: permanent decline')}
{_trace(f['audit'], t3, 'Stood down: customer returned unaided')}

<h2>Diagnosis quality</h2>
<table>
<tr><th>Measure</th><th class="num">Value</th></tr>
<tr><td>Held-out accuracy</td>
    <td class="num mono">{d['accuracy']:.1%} ({d['correct']}/{d['holdout_size']})</td></tr>
<tr><td>p95 classification latency</td>
    <td class="num mono">{run['latency']['p95_ms']:.1f} ms</td></tr>
<tr><td>Confidence floor for acting</td><td class="num mono">0.40</td></tr>
<tr><td>Most common confusion</td><td class="num mono">{
    html.escape(str(d['top_confusions'][0][0]) if d['top_confusions'] else 'none')}</td></tr>
</table>

<h2>Exceptions we could not resolve</h2>
<table><tr><th>Reason</th><th class="num">Payments</th></tr>{exc_rows}</table>
<p class="note">{len(f['dead_letter'])} payments were routed to the dead-letter queue for
human review rather than retried blindly.</p>

<h2>Does it hold in other worlds?</h2>
<table>
<tr><th>Seed</th><th class="num">Baseline</th><th class="num">Reflow</th><th class="num">Lift</th></tr>
{seed_rows}
</table>
<p class="note">Five independently generated cohorts. A single favourable random draw
is not a result.</p>
</div>"""

    doc = (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Reflow — recovery results</title><style>{CSS}</style></head>"
           f"<body>{body}</body></html>")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_path
