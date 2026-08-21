"""Razorpay test-mode adapter: seam contract, safety guard, webhook crypto.

No network anywhere — the client takes an injectable transport.
"""

import hashlib
import hmac
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from engine.datagen import generate_batch
from engine.models import ActionKind, Channel, Outcome
from engine.playbooks import Step
from engine.razorpay_world import (
    RazorpayClient, RazorpayError, RazorpayWorld, WebhookInbox,
    verify_webhook_signature,
)

START = datetime(2026, 8, 1, 9, 0)


class FakeTransport:
    """Records requests; replies from a canned queue."""

    def __init__(self, replies):
        self.calls = []
        self.replies = list(replies)

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.replies.pop(0)


def _client(replies):
    t = FakeTransport(replies)
    return RazorpayClient("rzp_test_abc123", "secret", transport=t), t


class ClientTest(unittest.TestCase):
    def test_refuses_live_keys_by_construction(self):
        with self.assertRaises(RazorpayError):
            RazorpayClient("rzp_live_abc123", "secret")

    def test_payment_link_request_shape(self):
        client, t = _client([(200, json.dumps(
            {"id": "plink_1", "short_url": "https://rzp.io/l/x"}).encode())])
        link = client.create_payment_link(50_000, "desc", "case_00001", "Aarav")
        self.assertEqual(link["short_url"], "https://rzp.io/l/x")
        method, url, headers, body = t.calls[0]
        self.assertEqual((method, url),
                         ("POST", "https://api.razorpay.com/v1/payment_links"))
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        payload = json.loads(body)
        self.assertEqual(payload["amount"], 50_000)
        self.assertEqual(payload["currency"], "INR")
        self.assertEqual(payload["notes"]["case_id"], "case_00001")

    def test_api_error_is_surfaced(self):
        client, _ = _client([(401, json.dumps(
            {"error": {"description": "bad key"}}).encode())])
        with self.assertRaises(RazorpayError) as ctx:
            client.create_order(100, "case_x")
        self.assertIn("bad key", str(ctx.exception))


class WebhookSignatureTest(unittest.TestCase):
    def test_valid_signature_accepted_tampered_rejected(self):
        secret, body = "whsec", b'{"event":"payment_link.paid"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(body, sig, secret))
        self.assertFalse(verify_webhook_signature(body + b" ", sig, secret))
        self.assertFalse(verify_webhook_signature(body, sig, "other"))
        self.assertFalse(verify_webhook_signature(body, "", secret))


def _paid_event(case_id):
    return {"event": "payment_link.paid", "payload": {"payment_link": {
        "entity": {"id": "plink_1", "notes": {"case_id": case_id}}}}}


class WorldSeamTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.inbox = WebhookInbox(Path(self.tmp.name) / "inbox.jsonl")
        self.case = generate_batch(1, 42, START)[0][0]

    def tearDown(self):
        self.tmp.cleanup()

    def test_payment_link_step_creates_link_and_awaits_webhook(self):
        client, t = _client([(200, json.dumps(
            {"id": "plink_1", "short_url": "https://rzp.io/l/x"}).encode())])
        world = RazorpayWorld(client, self.inbox)
        step = Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+0h")
        outcome, detail = world.perform(self.case, step, START)
        self.assertEqual(outcome, Outcome.NO_RESPONSE)
        self.assertIn("https://rzp.io/l/x", detail)

    def test_verified_paid_webhook_resolves_to_paid(self):
        self.inbox.append(_paid_event(self.case.id), START)
        world = RazorpayWorld(_client([])[0], self.inbox)
        step = Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+0h")
        outcome, detail = world.perform(self.case, step, START)
        self.assertEqual(outcome, Outcome.PAID)
        self.assertIn("payment_link.paid", detail)

    def test_other_cases_webhook_does_not_leak_across_cases(self):
        self.inbox.append(_paid_event("case_99999"), START)
        client, _ = _client([(200, json.dumps(
            {"id": "plink_2", "short_url": "https://rzp.io/l/y"}).encode())])
        world = RazorpayWorld(client, self.inbox)
        step = Step(ActionKind.NUDGE, Channel.WHATSAPP, "+0h")
        outcome, _ = world.perform(self.case, step, START)
        self.assertEqual(outcome, Outcome.NO_RESPONSE)

    def test_retry_creates_order_with_discounted_amount(self):
        client, t = _client([(200, json.dumps({"id": "order_1"}).encode())])
        world = RazorpayWorld(client, self.inbox)
        step = Step(ActionKind.RETRY, Channel.SMART_RETRY, "+0h")
        outcome, detail = world.perform(self.case, step, START, offer_bps=500)
        self.assertEqual(outcome, Outcome.NO_RESPONSE)
        self.assertIn("order_1", detail)
        payload = json.loads(t.calls[0][3])
        expected = self.case.amount_paise - self.case.amount_paise * 500 // 10_000
        self.assertEqual(payload["amount"], expected)


if __name__ == "__main__":
    unittest.main()
