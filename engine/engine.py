"""The recovery engine: an event-driven, clock-driven orchestrator.

Detect -> Diagnose -> Triage -> Treat, over a simulated clock, using a priority
queue of scheduled actions. Every transition including every action the
guardrails REFUSED is appended to the hash-chained audit ledger.
"""

from __future__ import annotations

import calendar
import heapq
from datetime import datetime, timedelta

from .brain import Narrator
from .config import Policy
from .datagen import CaseTruth
from .diagnose import diagnose
from .guardrails import Guardrails, Verdict
from .ledger import AuditLedger
from .models import (
    ActionKind, Attempt, Case, CaseStatus, Channel, CONTACT_CHANNELS,
    Outcome, Promise, fmt_inr,
)
from .playbooks import ALL_PLAYBOOKS, Playbook, Step, select_playbook
from .triage import triage
from .world import World

_SUCCESS_OUTCOMES = {Outcome.PAID, Outcome.PROMISE_MADE, Outcome.PROMISE_KEPT}


class RecoveryEngine:
    def __init__(self, policy: Policy, world: World, ledger: AuditLedger,
                 narrator: Narrator, start: datetime, seed: int):
        self.policy = policy
        self.world = world
        self.ledger = ledger
        self.narrator = narrator
        self.start = start
        self.seed = seed
        self.horizon_end = start + timedelta(days=policy.horizon_days)
        self.guardrails = Guardrails(policy)

        self._heap: list[tuple[float, int, str, int]] = []
        self._seq = 0
        self._cases: dict[str, Case] = {}
        self._defers: dict[tuple[str, int], int] = {}
        self._contact_log: dict[str, list[datetime]] = {}
        self._channel_stats: dict[Channel, list[int]] = {}  # [attempts, successes]
        self.guardrail_events: dict[str, int] = {}


    def _push(self, when: datetime, case_id: str, step_idx: int) -> None:
        heapq.heappush(self._heap, (when.timestamp(), self._seq, case_id, step_idx))
        self._seq += 1

    def _playbook(self, case: Case) -> Playbook:
        return ALL_PLAYBOOKS[case.playbook]

    def _resolve_delay(self, case: Case, step: Step, base: datetime) -> datetime:
        if step.delay == "salary_day":
            return self._next_salary_day(case.customer.salary_day, base)
        if step.delay == "promise_due":
            assert case.promise is not None
            return case.promise.due_at
        assert step.delay.startswith("+") and step.delay.endswith("h"), step.delay
        return base + timedelta(hours=int(step.delay[1:-1]))

    @staticmethod
    def _next_salary_day(salary_day: int, base: datetime) -> datetime:
        probe = base + timedelta(hours=12)
        for _ in range(64):
            days_in_month = calendar.monthrange(probe.year, probe.month)[1]
            if probe.day == min(salary_day, days_in_month):
                candidate = probe.replace(hour=10, minute=0, second=0, microsecond=0)
                if candidate > base:
                    return candidate
            probe += timedelta(days=1)
        return base + timedelta(days=3)  # unreachable safety net

    def _recent_contacts(self, customer_id: str, now: datetime) -> int:
        log = self._contact_log.get(customer_id, [])
        cutoff = now - timedelta(days=7)
        return sum(1 for t in log if t >= cutoff)

    def _count_rule(self, rule: str) -> None:
        self.guardrail_events[rule] = self.guardrail_events.get(rule, 0) + 1

    # -------------------------------------------------------------- lifecycle

    def run(self, cases: list[Case]) -> list[Case]:
        self.ledger.append("RUN_STARTED", at=self.start, payload={
            "seed": self.seed,
            "n_cases": len(cases),
            "horizon_days": self.policy.horizon_days,
            "policy": self.policy.to_dict(),
            "llm_narrator": self.narrator.llm_active,
        })

        for case in sorted(cases, key=lambda c: c.created_at):
            self._cases[case.id] = case
            self._intake(case)

        self._drain()

        for case in self._cases.values():
            if not case.terminal:
                self._finalise(case, CaseStatus.WRITTEN_OFF, "horizon_elapsed",
                               self.horizon_end)

        self.ledger.append("RUN_COMPLETED", at=self.horizon_end, payload={
            "recovered_paise": sum(c.recovered_paise for c in self._cases.values()),
            "spend_paise": sum(c.cost_paise for c in self._cases.values()),
            "guardrail_events": self.guardrail_events,
            "paused_channels": [c.value for c in self.guardrails.paused_channels],
        })
        return list(self._cases.values())

    def _intake(self, case: Case) -> None:
        self.ledger.append("CASE_DETECTED", case_id=case.id, at=case.created_at,
                           payload={
                               "leak_type": case.leak_type.value,
                               "amount": case.amount_paise,
                               "amount_h": fmt_inr(case.amount_paise),
                               "customer": case.customer.id,
                               "segment": case.customer.segment,
                               "signals": case.signals,
                           })
        case.diagnosis = diagnose(case)
        self.ledger.append("DIAGNOSED", case_id=case.id, at=case.created_at,
                           payload={
                               "root_cause": case.diagnosis.root_cause.value,
                               "confidence": case.diagnosis.confidence,
                               "recoverable": case.diagnosis.recoverable,
                               "narrative": case.diagnosis.narrative,
                           })
        case.triage = triage(case)
        self.ledger.append("TRIAGED", case_id=case.id, at=case.created_at,
                           payload={
                               "priority": case.triage.priority,
                               "expected_value": case.triage.expected_value_paise,
                           })

        playbook = select_playbook(case.diagnosis.root_cause) \
            if case.diagnosis.recoverable else None
        if playbook is None:
            self._finalise(case, CaseStatus.WRITTEN_OFF,
                           "unrecoverable_no_contact", case.created_at)
            return

        case.playbook = playbook.name
        case.status = CaseStatus.IN_RECOVERY
        self.ledger.append("PLAYBOOK_ASSIGNED", case_id=case.id,
                           at=case.created_at, payload={
                               "playbook": playbook.name,
                               "tagline": playbook.tagline,
                               "steps": len(playbook.steps),
                           })
        first_at = self._resolve_delay(case, playbook.steps[0], case.created_at)
        self._push(first_at, case.id, 0)


    def _drain(self) -> None:
        while self._heap:
            ts, _, case_id, step_idx = heapq.heappop(self._heap)
            now = datetime.fromtimestamp(ts)
            case = self._cases[case_id]
            if case.terminal:
                continue
            if now > self.horizon_end:
                continue  # case will be swept as horizon_elapsed
            step = self._playbook(case).steps[step_idx]
            verdict = self.guardrails.check(
                case, step, now, self._recent_contacts(case.customer.id, now))
            self._apply_verdict(case, step, step_idx, now, verdict)

    def _apply_verdict(self, case: Case, step: Step, step_idx: int,
                       now: datetime, verdict: Verdict) -> None:
        if verdict.action == "proceed":
            self._execute(case, step, step_idx, now, verdict)
            return

        self._count_rule(verdict.rule)
        if verdict.action == "defer":
            key = (case.id, step_idx)
            self._defers[key] = self._defers.get(key, 0) + 1
            if self._defers[key] > self.policy.max_defers_per_step or \
                    verdict.defer_until is None:
                self.ledger.append("ACTION_SKIPPED", case_id=case.id, at=now,
                                   payload={"step": step_idx,
                                            "rule": "max_defers_exceeded",
                                            "original_rule": verdict.rule})
                self._advance(case, step_idx, now)
                return
            self.ledger.append("ACTION_DEFERRED", case_id=case.id, at=now,
                               payload={"step": step_idx, **verdict.to_payload()})
            self._push(verdict.defer_until, case.id, step_idx)
        elif verdict.action == "skip":
            self.ledger.append("ACTION_SKIPPED", case_id=case.id, at=now,
                               payload={"step": step_idx, **verdict.to_payload()})
            self._advance(case, step_idx, now)
        elif verdict.action == "stop":
            self.ledger.append("GUARDRAIL_STOP", case_id=case.id, at=now,
                               payload={"step": step_idx, **verdict.to_payload()})
            self._finalise(case, CaseStatus.STOPPED, verdict.rule, now)


    def _execute(self, case: Case, step: Step, step_idx: int,
                 now: datetime, verdict: Verdict) -> None:
        cost = self.policy.action_cost(step.channel)
        case.cost_paise += cost
        message = self.narrator.draft(case, step, verdict.offer_bps) \
            if step.channel in CONTACT_CHANNELS else ""

        outcome, detail = self.world.perform(case, step, now,
                                             offer_bps=verdict.offer_bps)
        case.attempts.append(Attempt(step_idx, step.kind, step.channel, now,
                                     cost, outcome, detail))
        if step.channel in CONTACT_CHANNELS:
            self._contact_log.setdefault(case.customer.id, []).append(now)

        stats = self._channel_stats.setdefault(step.channel, [0, 0])
        stats[0] += 1
        if outcome in _SUCCESS_OUTCOMES:
            stats[1] += 1
        if self.guardrails.observe_channel(step.channel, stats[0], stats[1]):
            self.ledger.append("CHANNEL_PAUSED", at=now, payload={
                "channel": step.channel.value,
                "attempts": stats[0], "successes": stats[1],
                "rule": "circuit_breaker",
            })

        payload = {
            "step": step_idx, "kind": step.kind.value,
            "channel": step.channel.value, "note": step.note,
            "cost_paise": cost, "outcome": outcome.value, "detail": detail,
        }
        if verdict.offer_bps:
            payload["offer_bps"] = verdict.offer_bps
        if verdict.notes:
            payload["guardrail_notes"] = verdict.notes
        if message:
            payload["message"] = message
        self.ledger.append("ACTION_EXECUTED", case_id=case.id, at=now,
                           payload=payload)

        self._handle_outcome(case, step, step_idx, now, outcome, detail,
                             verdict.offer_bps)


    def _handle_outcome(self, case: Case, step: Step, step_idx: int,
                        now: datetime, outcome: Outcome, detail: str,
                        offer_bps: int) -> None:
        if outcome == Outcome.PAID:
            self._recover(case, now, offer_bps)
        elif outcome == Outcome.PROMISE_KEPT:
            assert case.promise is not None
            case.promise.kept = True
            self.ledger.append("PROMISE_RESOLVED", case_id=case.id, at=now,
                               payload={"kept": True})
            self._recover(case, now, 0)
        elif outcome == Outcome.PROMISE_BROKEN:
            assert case.promise is not None
            case.promise.kept = False
            self.ledger.append("PROMISE_RESOLVED", case_id=case.id, at=now,
                               payload={"kept": False})
            self._advance(case, step_idx, now)
        elif outcome == Outcome.PROMISE_MADE:
            due_days = int(detail.rsplit("|", 1)[1]) if "|" in detail else 4
            case.promise = Promise(made_at=now,
                                   due_at=now + timedelta(days=due_days),
                                   amount_paise=case.amount_paise)
            self.ledger.append("PROMISE_RECORDED", case_id=case.id, at=now,
                               payload={"due_at": case.promise.due_at.isoformat(),
                                        "amount": case.amount_paise})
            self._schedule_promise_check(case, step_idx, now)
        elif outcome == Outcome.DISPUTE_RAISED:
            case.frozen = True
            self.ledger.append("DISPUTE_RAISED", case_id=case.id, at=now,
                               payload={"action": "freeze_all_outreach"})
            self._finalise(case, CaseStatus.ESCALATED, "dispute_raised", now)
        elif outcome == Outcome.NOTICE_DELIVERED:
            case.flags["predebit_at"] = now
            self._advance(case, step_idx, now)
        elif outcome == Outcome.HANDED_TO_HUMAN:
            self._finalise(case, CaseStatus.ESCALATED, "handed_to_human", now)
        else:  # NO_RESPONSE
            self._advance(case, step_idx, now)

    def _schedule_promise_check(self, case: Case, step_idx: int,
                                now: datetime) -> None:
        steps = self._playbook(case).steps
        for idx in range(step_idx + 1, len(steps)):
            if steps[idx].kind == ActionKind.PROMISE_CHECK:
                self._push(case.promise.due_at, case.id, idx)
                return
        self._advance(case, step_idx, now)

    def _advance(self, case: Case, step_idx: int, now: datetime) -> None:
        steps = self._playbook(case).steps
        next_idx = step_idx + 1
        while next_idx < len(steps) and \
                steps[next_idx].kind == ActionKind.PROMISE_CHECK and \
                case.promise is None:
            next_idx += 1
        if next_idx >= len(steps):
            self._finalise(case, CaseStatus.WRITTEN_OFF, "playbook_exhausted", now)
            return
        when = self._resolve_delay(case, steps[next_idx], now)
        self._push(when, case.id, next_idx)

    def _recover(self, case: Case, now: datetime, offer_bps: int) -> None:
        discount = case.amount_paise * offer_bps // 10_000
        case.discount_paise += discount
        case.recovered_paise = case.amount_paise - discount
        case.recovered_at = now
        case.status = CaseStatus.RECOVERED
        self.ledger.append("CASE_RECOVERED", case_id=case.id, at=now, payload={
            "gross": case.amount_paise,
            "discount": discount,
            "net_recovered": case.recovered_paise,
            "net_recovered_h": fmt_inr(case.recovered_paise),
            "spend": case.cost_paise,
            "hours_to_recovery": round(
                (now - case.created_at).total_seconds() / 3600, 1),
        })

    def _finalise(self, case: Case, status: CaseStatus, reason: str,
                  now: datetime) -> None:
        case.status = status
        case.stop_reason = reason
        event = {
            CaseStatus.WRITTEN_OFF: "CASE_WRITTEN_OFF",
            CaseStatus.STOPPED: "CASE_STOPPED",
            CaseStatus.ESCALATED: "CASE_ESCALATED",
        }[status]
        self.ledger.append(event, case_id=case.id, at=now, payload={
            "reason": reason,
            "attempts": len(case.attempts),
            "spend": case.cost_paise,
        })
