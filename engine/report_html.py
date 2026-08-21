"""Self-contained HTML recovery dashboard (no external assets, light+dark)."""

from __future__ import annotations

import html
from pathlib import Path

from .models import fmt_inr

_CSS = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --good: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --good: #0ca30c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface-1: #1a1a19;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --good: #0ca30c;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--page); color: var(--text-primary);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
  padding: 28px 20px 60px;
}
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 2px; }
.sub { color: var(--text-secondary); margin: 0 0 24px; font-size: 14px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px; }
.tile {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px;
}
.tile .k { color: var(--muted); font-size: 12px; letter-spacing: .02em; text-transform: uppercase; }
.tile .v { font-size: 24px; font-weight: 650; margin-top: 2px; }
.tile .d { color: var(--text-secondary); font-size: 12px; margin-top: 2px; }
.tile .v.pos { color: var(--good); }
.card {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px; margin-bottom: 20px;
}
.card h2 { font-size: 15px; margin: 0 0 4px; }
.card .note { color: var(--text-secondary); font-size: 13px; margin: 0 0 14px; }
.legend { display: flex; gap: 16px; font-size: 12.5px; color: var(--text-secondary); margin-bottom: 12px; }
.legend .sw { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: baseline; }
.row { margin-bottom: 14px; }
.row .lbl { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 4px; }
.row .lbl .name { color: var(--text-primary); }
.row .lbl .val { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.track { position: relative; height: 18px; background: var(--grid); border-radius: 4px; overflow: hidden; }
.fill { position: absolute; inset: 0 auto 0 0; background: var(--series-1); border-radius: 4px 0 0 4px; border-right: 2px solid var(--surface-1); }
.pct { color: var(--good); font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th { text-align: left; color: var(--muted); font-weight: 500; font-size: 12px; text-transform: uppercase; letter-spacing: .02em; padding: 6px 8px; border-bottom: 1px solid var(--baseline); }
td { padding: 7px 8px; border-bottom: 1px solid var(--grid); color: var(--text-primary); }
td.num { text-align: right; font-variant-numeric: tabular-nums; color: var(--text-secondary); }
.overflow { overflow-x: auto; }
footer { color: var(--muted); font-size: 12px; margin-top: 26px; }
"""


def _tile(k: str, v: str, d: str = "", pos: bool = False) -> str:
    cls = "v pos" if pos else "v"
    return (f'<div class="tile"><div class="k">{html.escape(k)}</div>'
            f'<div class="{cls}">{html.escape(v)}</div>'
            f'<div class="d">{html.escape(d)}</div></div>')


def render(summary: dict, seed: int, out_path: Path) -> Path:
    s = summary
    tiles = "".join([
        _tile("Recovered", fmt_inr(s["recovered_paise"]),
              f'{s["recovered_cases"]} of {s["cases"]} cases', pos=True),
        _tile("Recovery rate", f'{s["recovery_rate_pct"]}%', "of value at risk"),
        _tile("At risk", fmt_inr(s["at_risk_paise"]), "detected in batch"),
        _tile("Spend", fmt_inr(s["spend_paise"]),
              f'ROI {s["roi"]}x, discounts {fmt_inr(s["discounts_paise"])}'),
        _tile("Escalated", str(s["by_status"].get("escalated", 0)),
              "compliant human handoffs"),
        _tile("Avg time to money", f'{s["avg_hours_to_recovery"]}h',
              f'{s["promises_kept"]}/{s["promises_made"]} promises kept'),
    ])

    max_risk = max((row["at_risk_paise"] for row in s["by_leak"].values()),
                   default=1)
    bars = []
    for row in s["by_leak"].values():
        track_w = max(6.0, 100.0 * row["at_risk_paise"] / max_risk)
        fill_w = 100.0 * row["recovered_paise"] / row["at_risk_paise"] \
            if row["at_risk_paise"] else 0.0
        bars.append(
            f'<div class="row"><div class="lbl">'
            f'<span class="name">{html.escape(row["label"])}</span>'
            f'<span class="val">{fmt_inr(row["recovered_paise"])} of '
            f'{fmt_inr(row["at_risk_paise"])} recovered, '
            f'<span class="pct">{row["rate_pct"]}%</span></span></div>'
            f'<div class="track" style="width:{track_w:.1f}%">'
            f'<div class="fill" style="width:{fill_w:.1f}%"></div></div></div>'
        )

    chan_rows = "".join(
        f'<tr><td>{html.escape(ch)}</td>'
        f'<td class="num">{row["attempts"]}</td>'
        f'<td class="num">{fmt_inr(row["cost_paise"])}</td></tr>'
        for ch, row in sorted(s["by_channel"].items(),
                              key=lambda kv: -kv[1]["attempts"])
    )
    guard_rows = "".join(
        f'<tr><td>{html.escape(rule)}</td><td class="num">{n}</td></tr>'
        for rule, n in s["guardrail_events"].items()
    ) or '<tr><td colspan="2">none fired</td></tr>'
    status_rows = "".join(
        f'<tr><td>{html.escape(k)}</td><td class="num">{v}</td></tr>'
        for k, v in sorted(s["by_status"].items(), key=lambda kv: -kv[1])
    )

    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sanjeevani Recovery Report</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Sanjeevani &middot; Recovery Report</h1>
<p class="sub">Batch of {s["cases"]} at-risk cases &middot; {s["recovery_rate_pct"]}%
of value revived &middot; deterministic run (seed {seed}) &middot; every action in the
hash-chained ledger</p>
<div class="tiles">{tiles}</div>
<div class="card"><h2>Recovered vs at-risk, by leak type</h2>
<p class="note">Track length is money at risk; the filled segment is money
actually recovered (net of discounts).</p>
<div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>Recovered</span>
<span><span class="sw" style="background:var(--grid)"></span>Still at risk</span></div>
{"".join(bars)}</div>
<div class="card"><h2>Actions by channel</h2>
<div class="overflow"><table><tr><th>Channel</th><th style="text-align:right">Attempts</th>
<th style="text-align:right">Cost</th></tr>{chan_rows}</table></div></div>
<div class="card"><h2>Guardrail interventions</h2>
<p class="note">Times the agent was told <em>no</em>: deferred for quiet
hours, blocked for consent, or stopped on negative expected value. Refusals
are audited exactly like actions.</p>
<div class="overflow"><table><tr><th>Rule</th><th style="text-align:right">Fired</th></tr>
{guard_rows}</table></div></div>
<div class="card"><h2>Case outcomes</h2>
<div class="overflow"><table><tr><th>Status</th><th style="text-align:right">Cases</th></tr>
{status_rows}</table></div></div>
<footer>Generated by Sanjeevani &middot; verify the audit trail with
<code>python -m engine verify --ledger out/ledger.jsonl</code></footer>
</div></body></html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    return out_path
