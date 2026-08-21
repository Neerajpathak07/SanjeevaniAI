"""Real-time mode: the same brain, a wall clock, live Razorpay events.

`python3 -m engine serve` runs the SAME RecoveryEngine that powers the batch
demo — same diagnosis, same playbooks, same 13 guardrails, same hash-chained
ledger — but driven by reality instead of a simulation:

* **Detection** polls test-mode Razorpay for failed payments (no public
  webhook URL needed; a laptop is enough). Each new failure becomes a Case
  the moment it is seen, its Razorpay error mapped into the same signal
  vocabulary the diagnostic rules already speak.
* **Treatment** executes through `RazorpayWorld`: real payment links and
  orders, test-mode keys only.
* **Recovery** is recognised when Razorpay reports a created payment link
  as paid (polled), or a signature-verified webhook lands in the inbox.
* **Time** is a virtual clock that can run faster than the wall
  (`--time-scale 60`: a "+6h" playbook delay fires in 6 minutes) so a full
  case lifecycle — including quiet-hour deferrals — is watchable live.
  Guardrails check the virtual clock, so the rules are never bypassed,
  only fast-forwarded.

The batch demo proves the brain's decisions over 200 cases; this module is
the same brain tackling cases as they happen.
"""

from __future__ import annotations

import heapq
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from .brain import Narrator
from .config import Policy
from .engine import RecoveryEngine
from .ledger import AuditLedger
from .models import Case, CaseStatus, Channel, Customer, LeakType, fmt_inr
from .razorpay_world import RazorpayClient, RazorpayError, RazorpayWorld, WebhookInbox


class VirtualClock:
    """Wall time, optionally accelerated. scale=1 is plain wall clock."""

    def __init__(self, scale: float = 1.0,
                 wall_fn: Callable[[], datetime] = datetime.now):
        self.scale = scale
        self._wall_fn = wall_fn
        self._anchor = wall_fn()

    def now(self) -> datetime:
        elapsed = self._wall_fn() - self._anchor
        return self._anchor + elapsed * self.scale


#: Razorpay error vocabulary -> the signal codes diagnose.py already speaks.
#: Matched as lowercase substrings over error_reason + error_description.
_ERROR_PATTERNS: list[tuple[str, str]] = [
    ("insufficient", "BANK_51_INSUFFICIENT_FUNDS"),
    ("expired", "BANK_54_EXPIRED_CARD"),
    ("otp", "3DS_OTP_TIMEOUT"),
    ("authentication", "3DS_OTP_TIMEOUT"),
    ("issuer", "GW_91_ISSUER_UNAVAILABLE"),
    ("unavailable", "GW_91_ISSUER_UNAVAILABLE"),
    ("downtime", "GW_91_ISSUER_UNAVAILABLE"),
    ("fraud", "RISK_59_SUSPECTED_FRAUD"),
    ("risk", "RISK_59_SUSPECTED_FRAUD"),
    ("mandate", "MANDATE_PAUSED_BY_PAYER"),
]


def map_razorpay_error(payment: dict) -> str:
    blob = " ".join(str(payment.get(k) or "") for k in
                    ("error_reason", "error_description", "error_code")).lower()
    for needle, code in _ERROR_PATTERNS:
        if needle in blob:
            return code
    return "GW_05_DO_NOT_HONOUR"   # ambiguous -> low-friction link recovery


def payment_to_case(payment: dict, now: datetime) -> Case:
    """A failed Razorpay payment becomes a Case in the engine's vocabulary."""
    email = payment.get("email") or ""
    name = (payment.get("notes") or {}).get("name") or \
        (email.split("@")[0] if email else "Customer")
    customer = Customer(
        id=f"cust_{payment.get('contact') or email or payment['id']}",
        name=name,
        segment="b2c",
        # checkout-provided identifiers imply these two channels
        consented_channels={Channel.EMAIL, Channel.SMS},
        salary_day=1,
    )
    return Case(
        id=f"case_{payment['id']}",
        leak_type=LeakType.PAYMENT_FAILURE,
        amount_paise=int(payment.get("amount", 0)),
        customer=customer,
        created_at=now,
        signals={
            "leak_type": LeakType.PAYMENT_FAILURE.value,
            "error_code": map_razorpay_error(payment),
            "razorpay_payment_id": payment["id"],
            "razorpay_error": payment.get("error_description") or "",
            "method": payment.get("method") or "",
        },
    )


class EchoLedger(AuditLedger):
    """The live ledger narrates every entry to the console as it chains."""

    def append(self, event, **kwargs):
        entry = super().append(event, **kwargs)
        at = (entry.get("at") or "")[:19].replace("T", " ")
        cid = entry.get("case_id") or "-"
        p = entry.get("payload", {})
        hint = p.get("detail") or p.get("rule") or p.get("root_cause") or \
            p.get("playbook") or p.get("net_recovered_h") or ""
        print(f"  [{at}] {event:<18} {cid:<22} {hint}")
        return entry


class LiveEngine(RecoveryEngine):
    """RecoveryEngine driven by ticks of a (possibly accelerated) clock.

    The batch engine drains its whole heap at once; here `tick()` pops only
    the steps that are due at virtual-now, after ingesting new failures and
    resolving paid links. Everything popped goes through the identical
    guardrail -> execute -> ledger path.
    """

    def __init__(self, policy: Policy, client: RazorpayClient,
                 ledger: AuditLedger, narrator: Narrator, clock: VirtualClock,
                 out_dir: Path, inbox: Optional[WebhookInbox] = None):
        self.clock = clock
        self.client = client
        inbox = inbox or WebhookInbox(out_dir / "webhook_inbox.jsonl")
        world = RazorpayWorld(client, inbox)
        super().__init__(policy, world, ledger, narrator,
                         start=clock.now(), seed=0)
        self._seen_path = out_dir / "seen_payments.json"
        self._seen: set[str] = set(
            json.loads(self._seen_path.read_text())
            if self._seen_path.exists() else [])
        self.ledger.append("SERVE_STARTED", at=self.start, payload={
            "policy": policy.to_dict(), "time_scale": clock.scale,
            "world": "razorpay_test_mode",
        })

    # ------------------------------------------------------------- ingest

    def ingest(self, vnow: datetime) -> int:
        """Poll Razorpay for failed payments not yet seen; intake each."""
        new = 0
        for payment in self.client.list_payments():
            if payment.get("status") != "failed" or payment["id"] in self._seen:
                continue
            self._seen.add(payment["id"])
            case = payment_to_case(payment, vnow)
            if case.id in self._cases:
                continue
            self._cases[case.id] = case
            self._intake(case)
            new += 1
        if new:
            self._seen_path.write_text(json.dumps(sorted(self._seen)))
        return new

    # ------------------------------------------------------------ resolve

    def resolve(self, vnow: datetime) -> int:
        """A created payment link reported paid by Razorpay closes its case."""
        recovered = 0
        for case in self._cases.values():
            if case.terminal:
                continue
            for link_id in case.flags.get("rzp_links", []):
                try:
                    link = self.client.fetch_payment_link(link_id)
                except RazorpayError:
                    continue
                if link.get("status") == "paid":
                    self._recover(case, vnow, 0)
                    recovered += 1
                    break
        return recovered

    # --------------------------------------------------------------- tick

    def tick(self) -> datetime:
        vnow = self.clock.now()
        self.ingest(vnow)
        self.resolve(vnow)
        while self._heap and self._heap[0][0] <= vnow.timestamp():
            ts, _, case_id, step_idx = heapq.heappop(self._heap)
            case = self._cases[case_id]
            if case.terminal:
                continue
            step = self._playbook(case).steps[step_idx]
            when = datetime.fromtimestamp(ts)
            verdict = self.guardrails.check(
                case, step, when, self._recent_contacts(case.customer.id, when))
            self._apply_verdict(case, step, step_idx, when, verdict)
        for case in self._cases.values():
            if not case.terminal and \
                    vnow > case.created_at + timedelta(days=self.policy.horizon_days):
                self._finalise(case, CaseStatus.WRITTEN_OFF,
                               "horizon_elapsed", vnow)
        return vnow

    # ------------------------------------------------------------ summary

    def snapshot(self) -> dict:
        cases = list(self._cases.values())
        return {
            "cases": len(cases),
            "recovered": sum(1 for c in cases
                             if c.status == CaseStatus.RECOVERED),
            "recovered_h": fmt_inr(sum(c.recovered_paise for c in cases)),
            "in_flight": sum(1 for c in cases if not c.terminal),
            "guardrail_events": dict(self.guardrail_events),
        }
