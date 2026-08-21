"""The dumb-dunning baseline: the strategy Sanjeevani replaces.

Same seeded world, same priors, same batch — but the recovery logic every
merchant already has: a nightly cron that retries everything on a fixed
cadence and blasts email + SMS to everyone, with no diagnosis, no consent
checks, no caps, no pre-debit notices, no dispute handling and no audit
trail.

The point is isolation: any delta between this and the engine is exactly
what diagnosis + guardrails + stopping rules add, because everything else
(world, seed, priors, fatigue decay) is held constant.

An offline policy auditor — with ground truth in hand — then walks the
action stream of BOTH agents and counts every action Sanjeevani's
policy-as-code would have refused. The engine's zero is measured by the
same code that scores the cron, not asserted.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from .config import Policy
from .datagen import CaseTruth
from .models import (
    ActionKind, Attempt, Case, CaseStatus, Channel, CONTACT_CHANNELS,
    LeakType, Outcome, RootCause,
)
from .playbooks import Step
from .world import World

#: the cron's clock: retries in the night batch, messages after settlement
RETRY_HOUR = 2            # 02:00 night batch, every second day
BLAST_HOUR, BLAST_MIN = 21, 30   # daily "reminder blast", right inside quiet hours

NAIVE_RETRY = Step(ActionKind.RETRY, Channel.SMART_RETRY, "+0h", "cron night retry")
NAIVE_EMAIL = Step(ActionKind.NUDGE, Channel.EMAIL, "+0h", "cron blast email")
NAIVE_SMS = Step(ActionKind.NUDGE, Channel.SMS, "+0h", "cron blast sms")


def _schedule(case: Case, horizon_end: datetime) -> list[tuple[datetime, Step]]:
    """One hopeful retry an hour in, then the daily cron until the horizon."""
    acts = [(case.created_at + timedelta(hours=1), NAIVE_RETRY)]
    day = 1
    while True:
        anchor = case.created_at + timedelta(days=day)
        if anchor > horizon_end:
            break
        if day % 2 == 0:
            acts.append((anchor.replace(hour=RETRY_HOUR, minute=0, second=0,
                                        microsecond=0), NAIVE_RETRY))
        blast = anchor.replace(hour=BLAST_HOUR, minute=BLAST_MIN, second=0,
                               microsecond=0)
        acts.append((blast, NAIVE_EMAIL))
        acts.append((blast + timedelta(minutes=2), NAIVE_SMS))
        day += 1
    return sorted((t, s) for t, s in acts if t <= horizon_end)


def run_naive(cases: list[Case], world: World, policy: Policy,
              start: datetime) -> list[Case]:
    """Run the dumb cron over the batch. Mutates and returns the cases."""
    horizon_end = start + timedelta(days=policy.horizon_days)
    for case in cases:
        case.status = CaseStatus.IN_RECOVERY
        for at, step in _schedule(case, horizon_end):
            cost = policy.action_cost(step.channel)
            case.cost_paise += cost
            if case.frozen and step.channel in CONTACT_CHANNELS:
                # a disputing buyer does not pay a dunning bot; the cron
                # doesn't know that and keeps burning money and goodwill
                outcome, detail = Outcome.NO_RESPONSE, "buyer disputing; blast ignored"
            else:
                outcome, detail = world.perform(case, step, at)
            case.attempts.append(Attempt(-1, step.kind, step.channel, at,
                                         cost, outcome, detail))
            if outcome == Outcome.DISPUTE_RAISED:
                case.frozen = True      # noted nowhere; the cron rolls on
            elif outcome == Outcome.PAID:
                case.recovered_paise = case.amount_paise
                case.recovered_at = at
                case.status = CaseStatus.RECOVERED
                break
        if case.status != CaseStatus.RECOVERED:
            case.status = CaseStatus.WRITTEN_OFF
    return cases


# --------------------------------------------------------------------------
# The policy auditor: replays any agent's action stream against policy,
# with ground truth available (it is an offline audit, not a live gate).
# --------------------------------------------------------------------------

def audit_policy(cases: list[Case], truths: dict[str, CaseTruth],
                 policy: Policy) -> dict:
    """Count actions that violate policy. Returns totals + per-rule breakdown."""
    events = sorted(
        ((a.at, c.id, i, c, a) for c in cases for i, a in enumerate(c.attempts)),
        key=lambda e: (e[0], e[1], e[2]))

    violations: Counter[str] = Counter()
    flagged = 0
    contact_hist: dict[str, list[datetime]] = {}
    case_contacts: Counter[str] = Counter()
    frozen_at: dict[str, datetime] = {}
    last_notice: dict[str, datetime] = {}

    for at, cid, _i, case, a in events:
        rules: list[str] = []
        is_contact = a.channel in CONTACT_CHANNELS

        if cid in frozen_at and at > frozen_at[cid]:
            rules.append("dispute_freeze")
        if is_contact:
            if not case.customer.consents_to(a.channel):
                rules.append("no_consent")
            if at.hour >= policy.quiet_hours_start or at.hour < policy.quiet_hours_end:
                rules.append("quiet_hours")
            if a.channel == Channel.VOICE_HINGLISH and not (
                    policy.voice_hours_start <= at.hour < policy.voice_hours_end):
                rules.append("voice_window")
            if case_contacts[cid] >= policy.max_contacts_per_case:
                rules.append("contact_cap")
            recent = [t for t in contact_hist.get(case.customer.id, [])
                      if t >= at - timedelta(days=7)]
            if len(recent) >= policy.max_contacts_per_week:
                rules.append("weekly_contact_cap")
        if a.kind == ActionKind.RETRY and \
                case.leak_type == LeakType.SUBSCRIPTION_RENEWAL_FAILURE and \
                truths[cid].root_cause == RootCause.MANDATE_PAUSED:
            notice = last_notice.get(cid)
            if notice is None:
                rules.append("predebit_missing")
            else:
                age_h = (at - notice).total_seconds() / 3600
                if age_h < policy.predebit_min_hours:
                    rules.append("predebit_cooling")
                elif age_h > policy.predebit_max_hours:
                    rules.append("predebit_stale")

        if is_contact:
            case_contacts[cid] += 1
            contact_hist.setdefault(case.customer.id, []).append(at)
        if a.kind == ActionKind.PRE_DEBIT_NOTICE and \
                a.outcome == Outcome.NOTICE_DELIVERED:
            last_notice[cid] = at
        if a.outcome == Outcome.DISPUTE_RAISED:
            frozen_at[cid] = at

        if rules:
            flagged += 1
            violations.update(rules)

    return {
        "total": sum(violations.values()),
        "actions_flagged": flagged,
        "by_rule": dict(sorted(violations.items(), key=lambda kv: -kv[1])),
    }


def side_stats(cases: list[Case]) -> dict:
    """The handful of numbers the comparison table prints per agent."""
    recovered = sum(c.recovered_paise for c in cases)
    at_risk = sum(c.amount_paise for c in cases)
    spend = sum(c.cost_paise for c in cases)
    promises = [c for c in cases if c.promise is not None]
    return {
        "at_risk_paise": at_risk,
        "recovered_paise": recovered,
        "recovery_rate_pct": round(100 * recovered / at_risk, 1) if at_risk else 0.0,
        "recovered_cases": sum(1 for c in cases if c.status == CaseStatus.RECOVERED),
        "spend_paise": spend,
        "roi": round(recovered / spend, 1) if spend else 0.0,
        "contacts": sum(1 for c in cases for a in c.attempts
                        if a.channel in CONTACT_CHANNELS),
        "disputes_provoked": sum(1 for c in cases for a in c.attempts
                                 if a.outcome == Outcome.DISPUTE_RAISED),
        "escalated_cases": sum(1 for c in cases if c.status == CaseStatus.ESCALATED),
        "promises_made": len(promises),
        "promises_kept": sum(1 for c in promises if c.promise.kept is True),
    }
