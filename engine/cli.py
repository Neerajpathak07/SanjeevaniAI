"""Command-line interface.

    python -m engine demo   --cases 200 --seed 42 --out out/
    python -m engine verify --ledger out/ledger.jsonl
    python -m engine case case_00042 --out out/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .brain import Narrator
from .config import DEFAULT_POLICY
from .datagen import generate_batch
from .engine import RecoveryEngine
from .ledger import AuditLedger
from .metrics import summarise
from .models import fmt_inr
from .report_html import render
from .world import World

SIM_START = datetime(2026, 8, 1, 9, 0)  # treated as IST

BANNER = r"""
   _____ ___    _   __     ____________ _    _____    _   ______
  / ___//   |  / | / /    / / ____/ __// |  / /   |  / | / /  _/
  \__ \/ /| | /  |/ /__  / / __/ / _/  | | / / /| | /  |/ // /
 ___/ / ___ |/ /|  / /_/ / /___/ /___  | |/ / ___ |/ /|  // /
/____/_/  |_/_/ |_/\____/_____/_____/  |___/_/  |_/_/ |_/___/

        the revenue resuscitation engine
"""


def _rule(char: str = "─", n: int = 66) -> str:
    return char * n


def cmd_demo(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "ledger.jsonl"

    print(BANNER)
    print(f"  batch={args.cases} cases  seed={args.seed}  "
          f"horizon={args.days} days  out={out_dir}/")

    policy = DEFAULT_POLICY
    policy.horizon_days = args.days
    cases, truths = generate_batch(args.cases, args.seed, SIM_START)
    narrator = Narrator(use_llm=False if args.no_llm else None)
    print(f"  message copy: "
          f"{'Claude (claude-opus-4-8)' if narrator.llm_active else 'built-in templates (offline)'}\n")

    ledger = AuditLedger(ledger_path)
    world = World(args.seed, truths, policy)
    engine = RecoveryEngine(policy, world, ledger, narrator, SIM_START, args.seed)
    finished = engine.run(cases)
    ledger.close()

    summary = summarise(finished, engine.guardrail_events,
                        [c.value for c in engine.guardrails.paused_channels])
    (out_dir / "report.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8")
    html_path = render(summary, args.seed, out_dir / "dashboard.html")

    _print_summary(summary)

    ok, msg = AuditLedger.verify(ledger_path)
    print(f"\n  audit ledger  {ledger_path}  ->  "
          f"{'✔ ' + msg if ok else '✘ ' + msg}")
    print(f"  dashboard     {html_path}")
    print(f"  metrics json  {out_dir / 'report.json'}\n")
    return 0


def _print_summary(s: dict) -> None:
    print("  " + _rule("═"))
    print("  MEASURED MONEY RECOVERED ACROSS THE BATCH")
    print("  " + _rule("═"))
    print(f"  at risk        {fmt_inr(s['at_risk_paise']):>16}   across {s['cases']} cases")
    print(f"  recovered      {fmt_inr(s['recovered_paise']):>16}   "
          f"({s['recovery_rate_pct']}% of value, {s['recovered_cases']} cases)")
    print(f"  spend          {fmt_inr(s['spend_paise']):>16}   "
          f"(ROI {s['roi']}×, discounts {fmt_inr(s['discounts_paise'])})")
    print(f"  net recovered  {fmt_inr(s['net_paise']):>16}")
    print(f"  avg time to ₹  {s['avg_hours_to_recovery']:>14.1f} h")

    print("\n  " + _rule())
    print(f"  {'LEAK TYPE':<26}{'AT RISK':>14}{'RECOVERED':>14}{'RATE':>8}")
    print("  " + _rule())
    for row in s["by_leak"].values():
        print(f"  {row['label']:<26}{fmt_inr(row['at_risk_paise']):>14}"
              f"{fmt_inr(row['recovered_paise']):>14}{row['rate_pct']:>7}%")

    print("\n  " + _rule())
    print("  GUARDRAILS the agent was told 'no', on the record")
    print("  " + _rule())
    for rule, n in s["guardrail_events"].items():
        print(f"  {rule:<40}{n:>6}×")
    if s["paused_channels"]:
        print(f"  circuit breaker paused: {', '.join(s['paused_channels'])}")

    print("\n  " + _rule())
    print("  OUTCOMES")
    print("  " + _rule())
    for status, n in sorted(s["by_status"].items(), key=lambda kv: -kv[1]):
        print(f"  {status:<26}{n:>6} cases")
    print(f"  promises to pay: {s['promises_made']} made, "
          f"{s['promises_kept']} kept, {s['promises_broken']} broken")


def cmd_verify(args: argparse.Namespace) -> int:
    ok, msg = AuditLedger.verify(Path(args.ledger))
    print(("✔ " if ok else "✘ ") + msg)
    return 0 if ok else 1


def cmd_case(args: argparse.Namespace) -> int:
    ledger_path = Path(args.out) / "ledger.jsonl"
    if not ledger_path.exists():
        print(f"no ledger at {ledger_path} run the demo first", file=sys.stderr)
        return 1
    found = False
    for entry in AuditLedger.read(ledger_path):
        if entry.get("case_id") != args.case_id:
            continue
        found = True
        at = (entry.get("at") or "")[:16].replace("T", " ")
        print(f"[{at}] {entry['event']}")
        for k, v in entry.get("payload", {}).items():
            print(f"    {k}: {v}")
    if not found:
        print(f"case {args.case_id!r} not found in {ledger_path}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine",
        description="Sanjeevani A revenue resuscitation engine",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    p_demo = sub.add_parser("demo", help="run a full recovery batch on synthetic data")
    p_demo.add_argument("--cases", type=int, default=200)
    p_demo.add_argument("--seed", type=int, default=42)
    p_demo.add_argument("--days", type=int, default=14)
    p_demo.add_argument("--out", default="out")
    p_demo.add_argument("--no-llm", action="store_true",
                        help="force template copy even if ANTHROPIC_API_KEY is set")
    p_demo.set_defaults(func=cmd_demo)

    p_verify = sub.add_parser("verify", help="verify the audit ledger hash chain")
    p_verify.add_argument("--ledger", default="out/ledger.jsonl")
    p_verify.set_defaults(func=cmd_verify)

    p_case = sub.add_parser("case", help="print one case's full audit timeline")
    p_case.add_argument("case_id")
    p_case.add_argument("--out", default="out")
    p_case.set_defaults(func=cmd_case)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
