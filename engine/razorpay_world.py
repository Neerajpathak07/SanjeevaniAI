"""Razorpay test-mode adapter: the same one-method seam, real API.

`world.py` (the simulator) and this module expose the identical contract —
``perform(case, step, now, offer_bps) -> (Outcome, detail)`` — so the engine
cannot tell them apart. That is the seam the whole architecture promises:
swap the world, keep the brain, the guardrails and the ledger.

Safety is structural, not procedural:

* **Test mode only.** The client refuses any key that does not start with
  ``rzp_test_``. No live money can move through this file, ever.
* **Stdlib only.** urllib + hmac; the zero-dependency claim holds.
* **Webhooks are verified.** The receiver checks Razorpay's
  ``X-Razorpay-Signature`` (HMAC-SHA256 over the raw body, constant-time
  compare) before an event is admitted to the inbox the world reads.

Wiring (all test mode — see README):

    export RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxx
    export RAZORPAY_KEY_SECRET=...
    python3 -m engine razorpay smoke              # create a real payment link
    RAZORPAY_WEBHOOK_SECRET=... \
    python3 -m engine razorpay webhook --port 8888  # verified event receiver
    python3 -m engine razorpay smoke              # re-run: link now shows PAID
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from .models import ActionKind, Case, CONTACT_CHANNELS, Outcome
from .playbooks import Step

API_BASE = "https://api.razorpay.com/v1"
TEST_KEY_PREFIX = "rzp_test_"

#: transport signature: (method, url, headers, body|None) -> (status, body_bytes)
Transport = Callable[[str, str, dict, Optional[bytes]], tuple[int, bytes]]


class RazorpayError(Exception):
    pass


def _urllib_transport(method: str, url: str, headers: dict,
                      body: Optional[bytes]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class RazorpayClient:
    """Thin, auditable wrapper over the two endpoints the seam needs."""

    def __init__(self, key_id: str, key_secret: str,
                 transport: Transport = _urllib_transport):
        if not key_id.startswith(TEST_KEY_PREFIX):
            raise RazorpayError(
                f"refusing key {key_id[:12]}…: only {TEST_KEY_PREFIX}* keys are "
                "accepted — this adapter is test-mode-only by construction")
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}",
                         "Content-Type": "application/json"}
        self._transport = transport

    def _call(self, method: str, path: str, payload: Optional[dict] = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        status, raw = self._transport(method, f"{API_BASE}{path}",
                                      dict(self._headers), body)
        try:
            data = json.loads(raw.decode() or "{}")
        except ValueError:
            raise RazorpayError(f"non-JSON response (HTTP {status})")
        if status >= 400:
            desc = data.get("error", {}).get("description", raw[:200].decode(errors="replace"))
            raise RazorpayError(f"HTTP {status}: {desc}")
        return data

    def create_payment_link(self, amount_paise: int, description: str,
                            case_id: str, customer_name: str) -> dict:
        return self._call("POST", "/payment_links", {
            "amount": amount_paise,
            "currency": "INR",
            "description": description[:255],
            "customer": {"name": customer_name},
            "notes": {"case_id": case_id, "agent": "sanjeevani"},
        })

    def create_order(self, amount_paise: int, case_id: str) -> dict:
        return self._call("POST", "/orders", {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": case_id,
            "notes": {"case_id": case_id, "agent": "sanjeevani"},
        })


# --------------------------------------------------------------------------
# Webhooks: verify first, then admit to the inbox the world reads.
# --------------------------------------------------------------------------

def verify_webhook_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


_PAID_EVENTS = {"payment_link.paid", "order.paid", "payment.captured"}


class WebhookInbox:
    """Append-only JSONL of signature-verified webhook events."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict, received_at: datetime) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"received_at": received_at.isoformat(),
                                 "event": event}, separators=(",", ":")) + "\n")

    @staticmethod
    def _case_id_of(event: dict) -> Optional[str]:
        for entity in (event.get("payload") or {}).values():
            notes = (entity or {}).get("entity", {}).get("notes", {})
            if isinstance(notes, dict) and notes.get("case_id"):
                return notes["case_id"]
        return None

    def paid_event_for(self, case_id: str) -> Optional[dict]:
        if not self.path.exists():
            return None
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                event = json.loads(line)["event"]
                if event.get("event") in _PAID_EVENTS and \
                        self._case_id_of(event) == case_id:
                    return event
        return None


def make_webhook_handler(inbox: WebhookInbox, secret: str, now_fn=datetime.now):
    """A BaseHTTPRequestHandler class bound to an inbox + secret."""
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            signature = self.headers.get("X-Razorpay-Signature", "")
            if not verify_webhook_signature(body, signature, secret):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"status":"invalid signature"}')
                return
            inbox.append(json.loads(body.decode()), now_fn())
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, fmt, *args):  # quieter default logging
            print(f"  [webhook] {fmt % args}")

    return Handler


# --------------------------------------------------------------------------
# The world itself: same contract as the simulator.
# --------------------------------------------------------------------------

class RazorpayWorld:
    """Real test-mode Razorpay behind the engine's world seam.

    Money-moving outcomes only ever come from verified webhook events in the
    inbox; an API call that merely *creates* a link or order reports
    NO_RESPONSE until the webhook says otherwise.
    """

    def __init__(self, client: RazorpayClient, inbox: WebhookInbox):
        self.client = client
        self.inbox = inbox

    def perform(self, case: Case, step: Step, now: datetime,
                offer_bps: int = 0) -> tuple[Outcome, str]:
        paid = self.inbox.paid_event_for(case.id)
        if paid:
            return Outcome.PAID, f"verified webhook {paid['event']} for {case.id}"

        if step.kind == ActionKind.ESCALATE_HUMAN:
            return Outcome.HANDED_TO_HUMAN, "dossier handed to human collections"
        if step.kind == ActionKind.PRE_DEBIT_NOTICE:
            return Outcome.NOTICE_DELIVERED, \
                "pre-debit notice recorded (deliver via your SMS/email provider)"
        if step.kind == ActionKind.PROMISE_CHECK:
            return Outcome.PROMISE_BROKEN, "no payment webhook by the promised date"

        amount = case.amount_paise - case.amount_paise * offer_bps // 10_000
        if step.kind == ActionKind.RETRY:
            order = self.client.create_order(amount, case.id)
            return Outcome.NO_RESPONSE, \
                f"order {order.get('id')} created; awaiting payment webhook"

        # NUDGE / PAYMENT_LINK on a contact channel: a real payment link
        link = self.client.create_payment_link(
            amount, f"Sanjeevani recovery — {case.leak_type.value} {case.id}",
            case.id, case.customer.name)
        via = step.channel.value if step.channel in CONTACT_CHANNELS else "link"
        return Outcome.NO_RESPONSE, \
            f"payment link {link.get('short_url')} created (via {via}); awaiting webhook"
