"""Command-line interface.

    python -m engine demo   --cases 200 --seed 42 --out out/
    python -m engine verify --ledger out/ledger.jsonl
    python -m engine case case_00042 --out out/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .baseline import audit_policy, run_naive, side_stats
from .brain import Narrator
from .config import DEFAULT_POLICY, Policy
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


def cmd_baseline(args: argparse.Namespace) -> int:
    """Same world, same seed: the dumb dunning cron vs the engine."""
    out_dir = Path(args.out) / "baseline"
    out_dir.mkdir(parents=True, exist_ok=True)
    policy = Policy()
    policy.horizon_days = args.days

    print(BANNER)
    print(f"  BASELINE DUEL  batch={args.cases} cases  seed={args.seed}  "
          f"horizon={args.days} days")
    print("  same world, same seed, same priors — only the brain differs\n")

    # the dumb cron
    naive_cases, naive_truths = generate_batch(args.cases, args.seed, SIM_START)
    naive_world = World(args.seed, naive_truths, policy)
    run_naive(naive_cases, naive_world, policy, SIM_START)
    naive = side_stats(naive_cases)
    naive_audit = audit_policy(naive_cases, naive_truths, policy)

    # the engine (offline narrator so the run is byte-stable)
    cases, truths = generate_batch(args.cases, args.seed, SIM_START)
    ledger = AuditLedger(out_dir / "sanjeevani_ledger.jsonl")
    engine = RecoveryEngine(policy, World(args.seed, truths, policy), ledger,
                            Narrator(use_llm=False), SIM_START, args.seed)
    finished = engine.run(cases)
    ledger.close()
    sanj = side_stats(finished)
    sanj_audit = audit_policy(finished, truths, policy)

    w = 20
    def row(label: str, a: str, b: str) -> None:
        print(f"  {label:<24}{a:>{w}}{b:>{w}}")

    print("  " + _rule("═"))
    row("", "DUMB CRON", "SANJEEVANI")
    print("  " + _rule("═"))
    row("recovered",
        f"{fmt_inr(naive['recovered_paise'])} ({naive['recovery_rate_pct']}%)",
        f"{fmt_inr(sanj['recovered_paise'])} ({sanj['recovery_rate_pct']}%)")
    row("spend", fmt_inr(naive["spend_paise"]), fmt_inr(sanj["spend_paise"]))
    row("ROI", f"{naive['roi']}×", f"{sanj['roi']}×")
    row("customer contacts", str(naive["contacts"]), str(sanj["contacts"]))
    row("disputes provoked", str(naive["disputes_provoked"]),
        str(sanj["disputes_provoked"]))
    row("human escalations", str(naive["escalated_cases"]),
        str(sanj["escalated_cases"]))
    row("promises kept",
        f"{naive['promises_kept']}/{naive['promises_made']}",
        f"{sanj['promises_kept']}/{sanj['promises_made']}")
    row("policy violations", str(naive_audit["total"]), str(sanj_audit["total"]))
    row("audit trail", "none (it's a cron)", "hash-chained ✔")

    print("\n  " + _rule())
    print("  CRON VIOLATIONS by policy rule (same auditor scored both agents)")
    print("  " + _rule())
    for rule, n in naive_audit["by_rule"].items():
        print(f"  {rule:<40}{n:>6}×")
    if sanj_audit["total"]:
        print("\n  ⚠ engine violations (should be zero — investigate!):")
        for rule, n in sanj_audit["by_rule"].items():
            print(f"  {rule:<40}{n:>6}×")

    comparison = {"seed": args.seed, "cases": args.cases, "days": args.days,
                  "dumb_cron": {**naive, "violations": naive_audit},
                  "sanjeevani": {**sanj, "violations": sanj_audit}}
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, default=str), encoding="utf-8")
    print(f"\n  comparison json  {out_dir / 'comparison.json'}\n")
    return 0


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


def cmd_razorpay(args: argparse.Namespace) -> int:
    from .models import ActionKind, Channel, Outcome
    from .playbooks import Step
    from .razorpay_world import (
        RazorpayClient, RazorpayError, RazorpayWorld, WebhookInbox,
        make_webhook_handler,
    )

    out_dir = Path(args.out) / "razorpay"
    inbox = WebhookInbox(out_dir / "webhook_inbox.jsonl")

    if args.action == "webhook":
        secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET")
        if not secret:
            print("set RAZORPAY_WEBHOOK_SECRET (from your Razorpay test-mode "
                  "webhook config) before starting the receiver", file=sys.stderr)
            return 1
        from http.server import HTTPServer
        server = HTTPServer(("127.0.0.1", args.port),
                            make_webhook_handler(inbox, secret))
        print(f"  verified-webhook receiver on http://127.0.0.1:{args.port}")
        print(f"  admitting signed events to {inbox.path}  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n  receiver stopped")
        return 0

    # action == "smoke": one real test-mode payment link through the seam
    key_id = os.environ.get("RAZORPAY_KEY_ID", "")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        print("Razorpay test-mode smoke needs credentials (free):\n"
              "  1. razorpay.com -> sign up -> stay in Test Mode\n"
              "  2. Settings -> API Keys -> Generate Test Key\n"
              "  3. export RAZORPAY_KEY_ID=rzp_test_...  "
              "RAZORPAY_KEY_SECRET=...\n"
              "then re-run: python3 -m engine razorpay smoke", file=sys.stderr)
        return 1

    try:
        client = RazorpayClient(key_id, key_secret)
    except RazorpayError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
    world = RazorpayWorld(client, inbox)

    cases, _truths = generate_batch(1, args.seed, SIM_START)
    case = cases[0]
    step = Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+0h",
                "smoke-test payment link")

    ledger = AuditLedger(out_dir / "ledger.jsonl")
    ledger.append("CASE_DETECTED", case_id=case.id, at=SIM_START, payload={
        "leak_type": case.leak_type.value, "amount": case.amount_paise,
        "amount_h": fmt_inr(case.amount_paise), "world": "razorpay_test_mode",
    })
    try:
        outcome, detail = world.perform(case, step, datetime.now())
    except RazorpayError as e:
        ledger.append("ACTION_FAILED", case_id=case.id, payload={"error": str(e)})
        ledger.close()
        print(f"✘ Razorpay API error: {e}", file=sys.stderr)
        return 1
    ledger.append("ACTION_EXECUTED", case_id=case.id, payload={
        "kind": step.kind.value, "channel": step.channel.value,
        "outcome": outcome.value, "detail": detail,
    })

    print(f"\n  case      {case.id}  {case.leak_type.value}  "
          f"{fmt_inr(case.amount_paise)}")
    print(f"  outcome   {outcome.value}")
    print(f"  detail    {detail}")
    if outcome == Outcome.PAID:
        ledger.append("CASE_RECOVERED", case_id=case.id, payload={
            "net_recovered": case.amount_paise, "via": "verified webhook"})
        print("\n  ✔ money observed via verified webhook — the seam is closed")
    else:
        print("\n  next: open the link and pay with a Razorpay test card,")
        print("  point a test-mode webhook (payment_link.paid) at the receiver")
        print("  (`python3 -m engine razorpay webhook`), then re-run this smoke —")
        print("  it will report PAID from the verified webhook inbox.")
    ledger.close()
    ok, msg = AuditLedger.verify(out_dir / "ledger.jsonl")
    print(f"  ledger    {out_dir / 'ledger.jsonl'} -> "
          f"{'✔ ' + msg if ok else '✘ ' + msg}\n")
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

    p_base = sub.add_parser(
        "baseline",
        help="duel: the dumb dunning cron vs the engine, same world & seed")
    p_base.add_argument("--cases", type=int, default=200)
    p_base.add_argument("--seed", type=int, default=42)
    p_base.add_argument("--days", type=int, default=14)
    p_base.add_argument("--out", default="out")
    p_base.set_defaults(func=cmd_baseline)

    p_verify = sub.add_parser("verify", help="verify the audit ledger hash chain")
    p_verify.add_argument("--ledger", default="out/ledger.jsonl")
    p_verify.set_defaults(func=cmd_verify)

    p_rzp = sub.add_parser(
        "razorpay",
        help="test-mode Razorpay through the same world seam (smoke | webhook)")
    p_rzp.add_argument("action", choices=["smoke", "webhook"])
    p_rzp.add_argument("--seed", type=int, default=42)
    p_rzp.add_argument("--port", type=int, default=8888)
    p_rzp.add_argument("--out", default="out")
    p_rzp.set_defaults(func=cmd_razorpay)

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
