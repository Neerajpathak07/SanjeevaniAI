"""The simulation world: the only component that sees ground truth.

Given an action the engine has already cleared through guardrails, the world
decides what the customer/bank actually does. Outcomes are drawn from the
same prior table the guardrails use for expected-value math, modulated by
each case's hidden temperament and every draw is keyed by
(seed, case, attempt#), so a run is bit-for-bit reproducible.

Swap this module for real Razorpay webhooks + channel providers and nothing
upstream changes: the engine only ever sees `(Outcome, detail)`.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta

from .config import (
    OFFER_MULTIPLIER, Policy, SALARY_WINDOW_MULTIPLIER, prior_success,
)
from .datagen import CaseTruth
from .models import (
    ActionKind, Case, Channel, LeakType, Outcome, RootCause,
)
from .playbooks import Step

MAX_P = 0.92
INVOICE_DISPUTE_ON_CHASE_P = 0.05


class World:
    def __init__(self, seed: int, truths: dict[str, CaseTruth], policy: Policy):
        self.seed = seed
        self.truths = truths
        self.policy = policy

    def _rng(self, case: Case) -> random.Random:
        key = f"{self.seed}:{case.id}:{len(case.attempts)}"
        digest = hashlib.sha256(key.encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def _in_salary_window(self, case: Case, now: datetime) -> bool:
        day = case.customer.salary_day
        for delta in (-1, 0, 1):
            probe = now + timedelta(days=delta)
            if probe.day == min(day, 28) or probe.day == day:
                return True
        return False

    def perform(self, case: Case, step: Step, now: datetime,
                offer_bps: int = 0) -> tuple[Outcome, str]:
        truth = self.truths[case.id]
        rng = self._rng(case)

        if step.kind == ActionKind.ESCALATE_HUMAN:
            return Outcome.HANDED_TO_HUMAN, "dossier handed to human collections"

        if step.kind == ActionKind.PRE_DEBIT_NOTICE:
            return Outcome.NOTICE_DELIVERED, "pre-debit notification delivered"

        if step.kind == ActionKind.PROMISE_CHECK:
            assert case.promise is not None
            kept = truth.promise_keeper and rng.random() < 0.9
            if kept:
                return Outcome.PROMISE_KEPT, "payment received on promised date"
            return Outcome.PROMISE_BROKEN, "promised date passed, no payment"

        # A chased-but-undisputed invoice can turn hostile mid-flight.
        if case.leak_type == LeakType.INVOICE_OVERDUE and \
                truth.root_cause == RootCause.INVOICE_LOST_IN_APPROVAL and \
                step.channel != Channel.SMART_RETRY and \
                rng.random() < INVOICE_DISPUTE_ON_CHASE_P:
            return Outcome.DISPUTE_RAISED, "buyer replied disputing the invoice"

        # Voice on a receivable usually lands a promise, not instant money.
        if case.leak_type == LeakType.INVOICE_OVERDUE and \
                step.channel == Channel.VOICE_HINGLISH:
            p = prior_success(truth.root_cause.value, "voice", step.channel)
            p = min(MAX_P, p * truth.responsiveness)
            if rng.random() < p:
                due_days = rng.randint(3, 6)
                return Outcome.PROMISE_MADE, f"AP head promised payment in {due_days} days|{due_days}"
            return Outcome.NO_RESPONSE, "call answered, no commitment obtained"

        # Everything else: does the money move?
        p = prior_success(truth.root_cause.value, step.kind.value, step.channel)
        p *= truth.responsiveness
        p *= self.policy.fatigue_decay ** max(0, case.contacts_made - 1)
        if step.kind == ActionKind.RETRY and \
                truth.root_cause == RootCause.INSUFFICIENT_FUNDS and \
                self._in_salary_window(case, now):
            p *= SALARY_WINDOW_MULTIPLIER
        if offer_bps > 0:
            p *= OFFER_MULTIPLIER
        p = min(MAX_P, p)

        if rng.random() < p:
            return Outcome.PAID, f"payment captured (p={p:.2f})"
        return Outcome.NO_RESPONSE, f"no movement (p={p:.2f})"
