"""Guardrails: every action passes through here before a rupee moves.

The playbook proposes; policy disposes. A verdict is one of:

  proceed   run the action (possibly with a clamped offer)
  defer     right action, wrong time (quiet hours, caps, stale notice)
  skip      this step is not allowed for this case; move to the next step
  stop      stop working the case entirely (stopping rule fired)

Every non-proceed verdict is written to the audit ledger by the engine, so
"why did the agent NOT act" is as auditable as "why did it act".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import Policy, prior_success
from .models import (
    ActionKind, Case, Channel, CONTACT_CHANNELS, LeakType, RootCause,
)
from .playbooks import Step


@dataclass
class Verdict:
    action: str                      # proceed | defer | skip | stop
    rule: str = "ok"
    reason: str = ""
    defer_until: Optional[datetime] = None
    offer_bps: int = 0
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "action": self.action,
            "rule": self.rule,
            "reason": self.reason,
            "defer_until": self.defer_until.isoformat() if self.defer_until else None,
            "offer_bps": self.offer_bps,
            "notes": self.notes,
        }


class Guardrails:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.paused_channels: set[Channel] = set()

    # -- time helpers ------------------------------------------------------

    def _in_quiet_hours(self, now: datetime) -> bool:
        h = now.hour
        return h >= self.policy.quiet_hours_start or h < self.policy.quiet_hours_end

    def _next_contact_window(self, now: datetime) -> datetime:
        end = self.policy.quiet_hours_end
        if now.hour >= self.policy.quiet_hours_start:
            base = (now + timedelta(days=1)).replace(hour=end, minute=0, second=0, microsecond=0)
        else:
            base = now.replace(hour=end, minute=0, second=0, microsecond=0)
        return base

    def _in_voice_window(self, now: datetime) -> bool:
        return self.policy.voice_hours_start <= now.hour < self.policy.voice_hours_end

    def _next_voice_window(self, now: datetime) -> datetime:
        start = self.policy.voice_hours_start
        candidate = now.replace(hour=start, minute=0, second=0, microsecond=0)
        if now.hour >= self.policy.voice_hours_end or candidate <= now:
            candidate = (now + timedelta(days=1)).replace(hour=start, minute=0, second=0, microsecond=0)
        return candidate

    # -- the gate ----------------------------------------------------------

    def check(self, case: Case, step: Step, now: datetime,
              recent_contacts: int) -> Verdict:
        """`recent_contacts`: customer-level contacts in the trailing 7 days."""
        p = self.policy
        is_contact = step.channel in CONTACT_CHANNELS

        # 1. A frozen case (dispute raised) is untouchable.
        if case.frozen:
            return Verdict("stop", "dispute_freeze",
                           "case is frozen pending dispute resolution no outreach")

        # 2. Escalation to a human is always permitted (it IS the safe exit).
        if step.kind == ActionKind.ESCALATE_HUMAN:
            return Verdict("proceed", "ok", "human escalation is always allowed")

        # 3. Hard attempt ceiling the workflow is bounded, full stop.
        if len(case.attempts) >= p.max_attempts_per_case:
            return Verdict("stop", "attempt_cap",
                           f"reached {p.max_attempts_per_case} total attempts")

        # 4. Consent: we only speak on channels the customer opted into.
        if p.require_channel_consent and is_contact and \
                not case.customer.consents_to(step.channel):
            return Verdict("skip", "no_consent",
                           f"customer has not consented to {step.channel.value}")

        # 5. Circuit breaker: a channel performing at noise level is paused.
        if step.channel in self.paused_channels:
            return Verdict("skip", "channel_breaker",
                           f"{step.channel.value} paused by circuit breaker")

        # 6. Per-case contact cap.
        if is_contact and case.contacts_made >= p.max_contacts_per_case:
            return Verdict("stop", "contact_cap",
                           f"already contacted {case.contacts_made}× on this case")

        # 7. Weekly per-customer contact cap (across all their cases).
        if is_contact and recent_contacts >= p.max_contacts_per_week:
            return Verdict("defer", "weekly_contact_cap",
                           f"{recent_contacts} contacts in trailing week",
                           defer_until=now + timedelta(hours=26))

        # 8. Quiet hours (TRAI-style): no messages 21:00–08:00.
        if is_contact and self._in_quiet_hours(now):
            return Verdict("defer", "quiet_hours",
                           "inside 21:00–08:00 quiet window",
                           defer_until=self._next_contact_window(now))

        # 9. Voice calls live in a narrower daytime window.
        if step.channel == Channel.VOICE_HINGLISH and not self._in_voice_window(now):
            return Verdict("defer", "voice_window",
                           "voice calls allowed 10:00–19:00 only",
                           defer_until=self._next_voice_window(now))

        # 10. RBI e-mandate: a re-presentation needs a fresh pre-debit notice.
        if step.kind == ActionKind.RETRY and \
                case.leak_type == LeakType.SUBSCRIPTION_RENEWAL_FAILURE and \
                case.diagnosis and case.diagnosis.root_cause == RootCause.MANDATE_PAUSED:
            notice_at = case.flags.get("predebit_at")
            if notice_at is None:
                return Verdict("skip", "predebit_missing",
                               "no pre-debit notice on record debit not permitted")
            age_h = (now - notice_at).total_seconds() / 3600
            if age_h < p.predebit_min_hours:
                return Verdict("defer", "predebit_cooling",
                               f"notice only {age_h:.0f}h old (<{p.predebit_min_hours}h)",
                               defer_until=notice_at + timedelta(hours=p.predebit_min_hours))
            if age_h > p.predebit_max_hours:
                return Verdict("skip", "predebit_stale",
                               f"notice {age_h:.0f}h old (>{p.predebit_max_hours}h) fresh notice required")

        cost = p.action_cost(step.channel)

        # 11. Small-ticket economics: don't burn ₹12 chasing ₹49.
        if case.amount_paise < p.small_ticket_paise and \
                cost > p.small_ticket_max_action_cost:
            return Verdict("skip", "small_ticket",
                           f"amount below ₹{p.small_ticket_paise // 100}; "
                           f"only near-free channels allowed")

        # 12. Expected-value stopping rule: cost must not exceed expected recovery.
        if p.expected_value_stop and cost > 0 and \
                step.kind not in (ActionKind.PRE_DEBIT_NOTICE, ActionKind.PROMISE_CHECK):
            cause = case.diagnosis.root_cause.value if case.diagnosis else ""
            confidence = case.diagnosis.confidence if case.diagnosis else 0.5
            p_est = prior_success(cause, step.kind.value, step.channel) * confidence
            p_est *= p.fatigue_decay ** case.contacts_made
            expected = p_est * case.amount_paise
            if cost > expected:
                return Verdict("skip", "negative_expected_value",
                               f"cost {cost}p > expected recovery {expected:.0f}p "
                               f"(p≈{p_est:.3f})")

        # 13. Offers are clamped to the policy ceiling, never blocked silently.
        verdict = Verdict("proceed", "ok", "all guardrails passed")
        if step.offer_bps:
            if step.offer_bps > p.max_discount_bps:
                verdict.offer_bps = p.max_discount_bps
                verdict.notes.append(
                    f"offer clamped {step.offer_bps}->{p.max_discount_bps} bps")
            else:
                verdict.offer_bps = step.offer_bps
        return verdict

    # -- circuit breaker ---------------------------------------------------

    def observe_channel(self, channel: Channel, attempts: int, successes: int) -> bool:
        """Feed channel stats; returns True if the breaker just tripped."""
        if channel in self.paused_channels:
            return False
        if attempts >= self.policy.breaker_min_attempts and \
                successes / attempts < self.policy.breaker_min_success_rate:
            self.paused_channels.add(channel)
            return True
        return False
