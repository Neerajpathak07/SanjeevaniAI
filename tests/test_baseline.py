"""The dumb-dunning baseline and the offline policy auditor."""

import unittest
from datetime import datetime, timedelta

from engine.baseline import audit_policy, run_naive, side_stats
from engine.brain import Narrator
from engine.config import Policy
from engine.datagen import CaseTruth, generate_batch
from engine.engine import RecoveryEngine
from engine.ledger import AuditLedger
from engine.models import (
    ActionKind, Attempt, Case, Channel, Customer, LeakType, Outcome, RootCause,
)
from engine.world import World

import tempfile
from pathlib import Path

START = datetime(2026, 8, 1, 9, 0)
SEED = 42
N = 200


def _run_naive(seed=SEED, n=N):
    policy = Policy()
    cases, truths = generate_batch(n, seed, START)
    run_naive(cases, World(seed, truths, policy), policy, START)
    return cases, truths, policy


def _run_engine(seed=SEED, n=N):
    policy = Policy()
    cases, truths = generate_batch(n, seed, START)
    with tempfile.TemporaryDirectory() as td:
        ledger = AuditLedger(Path(td) / "ledger.jsonl")
        eng = RecoveryEngine(policy, World(seed, truths, policy), ledger,
                             Narrator(use_llm=False), START, seed)
        finished = eng.run(cases)
        ledger.close()
    return finished, truths, policy


class NaiveBaselineTest(unittest.TestCase):
    def test_deterministic_for_a_seed(self):
        a = side_stats(_run_naive()[0])
        b = side_stats(_run_naive()[0])
        self.assertEqual(a, b)

    def test_cron_violates_policy_engine_does_not(self):
        naive_cases, naive_truths, policy = _run_naive()
        naive_audit = audit_policy(naive_cases, naive_truths, policy)
        self.assertGreater(naive_audit["total"], 0)
        self.assertIn("quiet_hours", naive_audit["by_rule"])
        self.assertIn("contact_cap", naive_audit["by_rule"])

        finished, truths, policy = _run_engine()
        engine_audit = audit_policy(finished, truths, policy)
        self.assertEqual(engine_audit["total"], 0,
                         f"engine violated policy: {engine_audit['by_rule']}")

    def test_engine_beats_cron_on_recovery_and_contact_burden(self):
        naive = side_stats(_run_naive()[0])
        sanj = side_stats(_run_engine()[0])
        self.assertGreater(sanj["recovered_paise"], naive["recovered_paise"])
        self.assertLess(sanj["contacts"], naive["contacts"])
        self.assertLess(sanj["spend_paise"], naive["spend_paise"])


class AuditorUnitTest(unittest.TestCase):
    """Feed the auditor hand-built action streams, one rule at a time."""

    def _case(self, consented=(Channel.EMAIL, Channel.SMS),
              leak=LeakType.PAYMENT_FAILURE):
        cust = Customer(id="cust_x", name="Test", segment="b2c",
                        consented_channels=set(consented), salary_day=1)
        return Case(id="case_x", leak_type=leak, amount_paise=100_000,
                    customer=cust, created_at=START)

    def _attempt(self, at, kind=ActionKind.NUDGE, channel=Channel.EMAIL,
                 outcome=Outcome.NO_RESPONSE):
        return Attempt(-1, kind, channel, at, 10, outcome)

    def _truths(self, case, cause=RootCause.INSUFFICIENT_FUNDS):
        return {case.id: CaseTruth(cause, 1.0, True, False)}

    def _audit(self, case, cause=RootCause.INSUFFICIENT_FUNDS):
        return audit_policy([case], self._truths(case, cause), Policy())

    def test_quiet_hours_flagged(self):
        case = self._case()
        case.attempts = [self._attempt(START.replace(hour=22))]
        self.assertEqual(self._audit(case)["by_rule"], {"quiet_hours": 1})

    def test_daytime_consented_contact_is_clean(self):
        case = self._case()
        case.attempts = [self._attempt(START.replace(hour=11))]
        self.assertEqual(self._audit(case)["total"], 0)

    def test_no_consent_flagged(self):
        case = self._case(consented=(Channel.EMAIL,))
        case.attempts = [self._attempt(START.replace(hour=11),
                                       channel=Channel.SMS)]
        self.assertEqual(self._audit(case)["by_rule"], {"no_consent": 1})

    def test_contact_cap_flagged_beyond_fourth(self):
        case = self._case()
        case.attempts = [
            self._attempt(START.replace(hour=10) + timedelta(days=7 * i))
            for i in range(6)  # weekly spacing keeps the weekly cap quiet
        ]
        self.assertEqual(self._audit(case)["by_rule"], {"contact_cap": 2})

    def test_dispute_freeze_flagged_after_dispute(self):
        case = self._case(leak=LeakType.INVOICE_OVERDUE)
        case.attempts = [
            self._attempt(START.replace(hour=10),
                          outcome=Outcome.DISPUTE_RAISED),
            self._attempt(START.replace(hour=12)),
        ]
        audit = self._audit(case, RootCause.INVOICE_LOST_IN_APPROVAL)
        self.assertEqual(audit["by_rule"], {"dispute_freeze": 1})

    def test_mandate_retry_without_notice_flagged(self):
        case = self._case(leak=LeakType.SUBSCRIPTION_RENEWAL_FAILURE)
        case.attempts = [self._attempt(START.replace(hour=3),
                                       kind=ActionKind.RETRY,
                                       channel=Channel.SMART_RETRY)]
        audit = self._audit(case, RootCause.MANDATE_PAUSED)
        self.assertEqual(audit["by_rule"], {"predebit_missing": 1})

    def test_mandate_retry_inside_notice_window_is_clean(self):
        case = self._case(leak=LeakType.SUBSCRIPTION_RENEWAL_FAILURE)
        notice_at = START.replace(hour=10)
        case.attempts = [
            self._attempt(notice_at, kind=ActionKind.PRE_DEBIT_NOTICE,
                          channel=Channel.SMS,
                          outcome=Outcome.NOTICE_DELIVERED),
            self._attempt(notice_at + timedelta(hours=30),
                          kind=ActionKind.RETRY, channel=Channel.SMART_RETRY),
        ]
        self.assertEqual(self._audit(case, RootCause.MANDATE_PAUSED)["total"], 0)


if __name__ == "__main__":
    unittest.main()
