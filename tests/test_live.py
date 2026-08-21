"""Real-time mode: ingestion, treatment, resolution — no network, no sleep.

The Razorpay client gets a scripted transport and the virtual clock gets a
hand-cranked wall function, so a full case lifecycle (failed payment seen ->
diagnosed -> real link 'created' -> link paid -> recovered) runs as plain
synchronous ticks.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from engine.brain import Narrator
from engine.config import Policy
from engine.ledger import AuditLedger
from engine.live import (
    LiveEngine, VirtualClock, map_razorpay_error, payment_to_case,
)
from engine.models import CaseStatus, RootCause
from engine.razorpay_world import RazorpayClient

START = datetime(2026, 8, 21, 11, 0)   # daytime: outside every quiet window


def _failed_payment(pid="pay_001", reason="insufficient balance in account"):
    return {"id": pid, "status": "failed", "amount": 250_000,
            "email": "aarav@example.com", "contact": "+919900000001",
            "error_reason": "payment_failed", "error_description": reason,
            "method": "card", "notes": {}}


class ScriptedRazorpay:
    """Transport that answers GET/POST from mutable in-memory state."""

    def __init__(self):
        self.payments = []
        self.links = {}          # link_id -> status
        self._n = 0

    def __call__(self, method, url, headers, body):
        if method == "GET" and "/payments" in url:
            return 200, json.dumps({"items": self.payments}).encode()
        if method == "GET" and "/payment_links/" in url:
            link_id = url.rsplit("/", 1)[1]
            return 200, json.dumps(
                {"id": link_id, "status": self.links.get(link_id, "created")}
            ).encode()
        if method == "POST" and url.endswith("/payment_links"):
            self._n += 1
            link_id = f"plink_{self._n}"
            self.links[link_id] = "created"
            return 200, json.dumps(
                {"id": link_id, "short_url": f"https://rzp.io/l/{link_id}"}
            ).encode()
        if method == "POST" and url.endswith("/orders"):
            self._n += 1
            return 200, json.dumps({"id": f"order_{self._n}"}).encode()
        raise AssertionError(f"unexpected call {method} {url}")


class HandClock:
    def __init__(self, start=START):
        self.wall = start

    def advance(self, **kw):
        self.wall += timedelta(**kw)

    def __call__(self):
        return self.wall


def _engine(tmp, rzp, clock, scale=1.0):
    client = RazorpayClient("rzp_test_x", "s", transport=rzp)
    vclock = VirtualClock(scale=scale, wall_fn=clock)
    ledger = AuditLedger(Path(tmp) / "ledger.jsonl", resume=True)
    return LiveEngine(Policy(), client, ledger, Narrator(use_llm=False),
                      vclock, Path(tmp))


class ErrorMappingTest(unittest.TestCase):
    def test_razorpay_errors_map_to_diagnostic_vocabulary(self):
        self.assertEqual(map_razorpay_error(_failed_payment()),
                         "BANK_51_INSUFFICIENT_FUNDS")
        self.assertEqual(
            map_razorpay_error({"error_description": "card expired"}),
            "BANK_54_EXPIRED_CARD")
        self.assertEqual(
            map_razorpay_error({"error_reason": "otp verification failed"}),
            "3DS_OTP_TIMEOUT")
        self.assertEqual(
            map_razorpay_error({"error_description": "issuer unavailable"}),
            "GW_91_ISSUER_UNAVAILABLE")
        self.assertEqual(
            map_razorpay_error({"error_description": "suspected fraud"}),
            "RISK_59_SUSPECTED_FRAUD")
        self.assertEqual(map_razorpay_error({}), "GW_05_DO_NOT_HONOUR")

    def test_payment_becomes_case_with_amount_and_signals(self):
        case = payment_to_case(_failed_payment(), START)
        self.assertEqual(case.amount_paise, 250_000)
        self.assertEqual(case.signals["error_code"],
                         "BANK_51_INSUFFICIENT_FUNDS")
        self.assertEqual(case.signals["razorpay_payment_id"], "pay_001")


class LiveLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = self.tmp_ctx.name
        self.rzp = ScriptedRazorpay()
        self.clock = HandClock()
        self.engines = []

    def tearDown(self):
        for eng in self.engines:
            eng.ledger.close()
        self.tmp_ctx.cleanup()

    def _engine(self, scale=1.0):
        eng = _engine(self.tmp, self.rzp, self.clock, scale=scale)
        self.engines.append(eng)
        return eng

    def test_failed_payment_is_detected_and_diagnosed_once(self):
        self.rzp.payments = [_failed_payment()]
        eng = self._engine()
        eng.tick()
        eng.tick()   # same payment again: must not double-ingest
        cases = list(eng._cases.values())
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].diagnosis.root_cause,
                         RootCause.INSUFFICIENT_FUNDS)
        self.assertEqual(cases[0].playbook, "payday_patrol")
        eng.ledger.close()
        ok, _ = AuditLedger.verify(Path(self.tmp) / "ledger.jsonl")
        self.assertTrue(ok)

    def test_due_step_executes_and_creates_real_link_artifacts(self):
        self.rzp.payments = [_failed_payment(
            reason="card expired", pid="pay_ex")]
        eng = self._engine()
        eng.tick()
        case = next(iter(eng._cases.values()))
        self.assertEqual(case.playbook, "card_transplant")
        self.clock.advance(hours=2)   # card_transplant step 0 is "+1h"
        eng.tick()
        self.assertEqual(len(case.attempts), 1)
        self.assertTrue(case.flags.get("rzp_links"))

    def test_paid_link_resolves_case_to_recovered(self):
        self.rzp.payments = [_failed_payment(reason="card expired",
                                             pid="pay_ex")]
        eng = self._engine()
        eng.tick()
        self.clock.advance(hours=2)
        eng.tick()
        case = next(iter(eng._cases.values()))
        link_id = case.flags["rzp_links"][0]
        self.rzp.links[link_id] = "paid"     # customer pays the real link
        self.clock.advance(minutes=5)
        eng.tick()
        self.assertEqual(case.status, CaseStatus.RECOVERED)
        self.assertEqual(case.recovered_paise, case.amount_paise)

    def test_time_scale_accelerates_playbook_delays(self):
        self.rzp.payments = [_failed_payment(reason="card expired",
                                             pid="pay_ex")]
        eng = self._engine(scale=60.0)
        eng.tick()
        case = next(iter(eng._cases.values()))
        self.clock.advance(minutes=2)   # 2 wall-min * 60 = 2 virtual hours
        eng.tick()
        self.assertEqual(len(case.attempts), 1)

    def test_seen_payments_survive_restart(self):
        self.rzp.payments = [_failed_payment()]
        eng = self._engine()
        eng.tick()
        eng.ledger.close()
        eng2 = self._engine()   # restart, same dir
        eng2.tick()
        self.assertEqual(len(eng2._cases), 0)   # not re-ingested
        eng2.ledger.close()
        ok, msg = AuditLedger.verify(Path(self.tmp) / "ledger.jsonl")
        self.assertTrue(ok, msg)   # one continuous chain across restarts


class LedgerResumeTest(unittest.TestCase):
    def test_resumed_ledger_continues_the_chain(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "ledger.jsonl"
            first = AuditLedger(path)
            first.append("A", payload={"n": 1})
            first.close()
            second = AuditLedger(path, resume=True)
            second.append("B", payload={"n": 2})
            second.close()
            ok, msg = AuditLedger.verify(path)
            self.assertTrue(ok, msg)
            self.assertIn("2 entries", msg)


if __name__ == "__main__":
    unittest.main()
