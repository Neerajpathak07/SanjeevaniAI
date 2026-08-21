"""End-to-end properties of a full batch run."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from engine.brain import Narrator
from engine.config import Policy
from engine.datagen import generate_batch
from engine.engine import RecoveryEngine
from engine.ledger import AuditLedger
from engine.metrics import summarise
from engine.models import (
    ActionKind, CaseStatus, Channel, CONTACT_CHANNELS, LeakType, RootCause,
)
from engine.world import World

START = datetime(2026, 8, 1, 9, 0)


def run_batch(seed=7, n=120, tmp=None):
    tmp = tmp or Path(tempfile.mkdtemp())
    policy = Policy()
    cases, truths = generate_batch(n, seed, START)
    ledger = AuditLedger(tmp / "ledger.jsonl")
    engine = RecoveryEngine(policy, World(seed, truths, policy), ledger,
                            Narrator(use_llm=False), START, seed)
    finished = engine.run(cases)
    ledger.close()
    return finished, engine, tmp / "ledger.jsonl", policy


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.engine, cls.ledger_path, cls.policy = run_batch()

    def test_money_is_actually_recovered(self):
        recovered = sum(c.recovered_paise for c in self.cases)
        at_risk = sum(c.amount_paise for c in self.cases)
        self.assertGreater(recovered, 0)
        self.assertLessEqual(recovered, at_risk)

    def test_every_case_reaches_a_terminal_state(self):
        for case in self.cases:
            self.assertTrue(case.terminal, f"{case.id} ended {case.status}")

    def test_workflow_is_bounded(self):
        for case in self.cases:
            self.assertLessEqual(len(case.attempts),
                                 self.policy.max_attempts_per_case)
            self.assertLessEqual(case.contacts_made,
                                 self.policy.max_contacts_per_case)

    def test_no_contact_inside_quiet_hours(self):
        for case in self.cases:
            for a in case.attempts:
                if a.channel in CONTACT_CHANNELS:
                    self.assertTrue(
                        self.policy.quiet_hours_end <= a.at.hour
                        < self.policy.quiet_hours_start,
                        f"{case.id} contacted at {a.at}")

    def test_risk_declined_payers_are_never_contacted(self):
        for case in self.cases:
            if case.diagnosis and \
                    case.diagnosis.root_cause == RootCause.RISK_DECLINE:
                self.assertEqual(len(case.attempts), 0)
                self.assertEqual(case.status, CaseStatus.WRITTEN_OFF)

    def test_mandate_retries_always_follow_a_valid_notice(self):
        for case in self.cases:
            if case.leak_type != LeakType.SUBSCRIPTION_RENEWAL_FAILURE:
                continue
            if not case.diagnosis or \
                    case.diagnosis.root_cause != RootCause.MANDATE_PAUSED:
                continue
            notices = []
            for a in case.attempts:
                if a.kind == ActionKind.PRE_DEBIT_NOTICE:
                    notices.append(a.at)
                elif a.kind == ActionKind.RETRY:
                    valid = [t for t in notices
                             if timedelta(hours=24) <= a.at - t
                             <= timedelta(hours=72)]
                    self.assertTrue(valid,
                                    f"{case.id} retried without a valid notice")

    def test_disputed_invoices_go_to_human_without_dunning(self):
        for case in self.cases:
            if case.diagnosis and \
                    case.diagnosis.root_cause == RootCause.INVOICE_DISPUTED:
                self.assertEqual(case.status, CaseStatus.ESCALATED)
                nudges = [a for a in case.attempts
                          if a.channel in CONTACT_CHANNELS]
                self.assertEqual(len(nudges), 0,
                                 f"{case.id} was dunned while disputed")

    def test_ledger_verifies_and_covers_every_case(self):
        ok, msg = AuditLedger.verify(self.ledger_path)
        self.assertTrue(ok, msg)
        seen = {e["case_id"] for e in AuditLedger.read(self.ledger_path)
                if e["case_id"]}
        self.assertEqual(seen, {c.id for c in self.cases})

    def test_summary_shape(self):
        s = summarise(self.cases, self.engine.guardrail_events, [])
        self.assertEqual(s["cases"], len(self.cases))
        self.assertGreater(s["recovery_rate_pct"], 0)
        self.assertIn("quiet_hours", s["guardrail_events"])


class DeterminismTest(unittest.TestCase):
    def test_same_seed_same_ledger_head(self):
        def head(path):
            last = None
            for entry in AuditLedger.read(path):
                last = entry
            return last["hash"]

        _, _, p1, _ = run_batch(seed=11, n=60)
        _, _, p2, _ = run_batch(seed=11, n=60)
        self.assertEqual(head(p1), head(p2))

    def test_different_seed_different_ledger(self):
        def head(path):
            last = None
            for entry in AuditLedger.read(path):
                last = entry
            return last["hash"]

        _, _, p1, _ = run_batch(seed=11, n=60)
        _, _, p2, _ = run_batch(seed=12, n=60)
        self.assertNotEqual(head(p1), head(p2))


if __name__ == "__main__":
    unittest.main()
