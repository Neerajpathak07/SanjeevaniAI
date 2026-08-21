"""Bounded recovery playbooks.

A playbook is a *finite* list of steps the workflow can never run away,
because there is nothing after the last step except a write-off. Delays are
declarative ("+6h", "salary_day", "promise_due") and resolved by the engine
against the simulated clock.

Every step still passes through guardrails before it executes; a playbook
proposes, policy disposes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ActionKind, Channel, RootCause


@dataclass(frozen=True)
class Step:
    kind: ActionKind
    channel: Channel
    delay: str                 # "+<n>h" | "salary_day" | "promise_due"
    note: str = ""
    offer_bps: int = 0         # winback discount, guardrail-capped


@dataclass(frozen=True)
class Playbook:
    name: str
    tagline: str
    steps: tuple[Step, ...] = field(default_factory=tuple)


LAZARUS_RETRY = Playbook(
    "lazarus_retry", "the bank was down, the money wasn't",
    (
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+2h", "first retry after issuer recovery window"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+8h", "second retry, off-peak"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+20h", "final silent retry"),
        Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+6h", "fresh link once silent retries are spent"),
    ),
)

PAYDAY_PATROL = Playbook(
    "payday_patrol", "retry when the salary lands, not when the cron fires",
    (
        Step(ActionKind.NUDGE, Channel.EMAIL, "+3h", "heads-up that the charge failed"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "salary_day", "salary-window retry at 10:00"),
        Step(ActionKind.PAYMENT_LINK, Channel.WHATSAPP, "+30h", "one-tap link after payday retry"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+72h", "last retry before write-off"),
    ),
)

CARD_TRANSPLANT = Playbook(
    "card_transplant", "no retry fixes expired plastic capture a new card",
    (
        Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+1h", "link to update instrument & pay"),
        Step(ActionKind.PAYMENT_LINK, Channel.SMS, "+48h", "short-code reminder with same link"),
        Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+96h", "voice nudge for high-intent payers"),
    ),
)

SECOND_TAP = Playbook(
    "second_tap", "intent was real, the OTP screen won offer one more tap",
    (
        Step(ActionKind.PAYMENT_LINK, Channel.WHATSAPP, "+1h", "one-tap resume link while intent is warm"),
        Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+24h", "same link, colder channel"),
    ),
)

WINBACK = Playbook(
    "winback", "the cart is not dead, it is resting",
    (
        Step(ActionKind.NUDGE, Channel.EMAIL, "+1h", "cart is saved plain reminder"),
        Step(ActionKind.NUDGE, Channel.WHATSAPP, "+26h", "reminder with delivery reassurance"),
        Step(ActionKind.NUDGE, Channel.EMAIL, "+74h", "final nudge with bounded sweetener", offer_bps=500),
    ),
)

MANDATE_CPR = Playbook(
    "mandate_cpr", "RBI-clean re-presentation: notice first, debit later",
    (
        Step(ActionKind.PRE_DEBIT_NOTICE, Channel.SMS, "+2h", "pre-debit notification (RBI e-mandate)"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+26h", "re-presentation ≥24h after notice"),
        Step(ActionKind.NUDGE, Channel.WHATSAPP, "+22h", "explain the pause, invite resume"),
        Step(ActionKind.PRE_DEBIT_NOTICE, Channel.SMS, "+48h", "fresh notice (previous one stale)"),
        Step(ActionKind.RETRY, Channel.SMART_RETRY, "+26h", "second compliant re-presentation"),
        Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+24h", "voice save attempt before churn"),
    ),
)

RECEIVABLES_LADDER = Playbook(
    "receivables_ladder", "polite -> firm -> human, with promises tracked",
    (
        Step(ActionKind.NUDGE, Channel.EMAIL, "+2h", "polite reminder with invoice copy"),
        Step(ActionKind.PAYMENT_LINK, Channel.EMAIL, "+72h", "statement of account + payment link"),
        Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+72h", "call accounts payable; record promise-to-pay"),
        Step(ActionKind.PROMISE_CHECK, Channel.SMART_RETRY, "promise_due", "verify the promise on its due date"),
        Step(ActionKind.ESCALATE_HUMAN, Channel.HUMAN_AGENT, "+48h", "hand to collections with full dossier"),
    ),
)

DISPUTE_FIREBREAK = Playbook(
    "dispute_firebreak", "a disputed invoice is a conversation, not a campaign",
    (
        Step(ActionKind.ESCALATE_HUMAN, Channel.HUMAN_AGENT, "+1h", "freeze outreach; human reviews the dispute"),
    ),
)


_BY_CAUSE: dict[RootCause, Playbook] = {
    RootCause.ISSUER_DOWNTIME: LAZARUS_RETRY,
    RootCause.INSUFFICIENT_FUNDS: PAYDAY_PATROL,
    RootCause.EXPIRED_INSTRUMENT: CARD_TRANSPLANT,
    RootCause.OTP_TIMEOUT: SECOND_TAP,
    RootCause.PRICE_HESITATION: WINBACK,
    RootCause.MANDATE_PAUSED: MANDATE_CPR,
    RootCause.INVOICE_LOST_IN_APPROVAL: RECEIVABLES_LADDER,
    RootCause.INVOICE_DISPUTED: DISPUTE_FIREBREAK,
}

ALL_PLAYBOOKS = {p.name: p for p in _BY_CAUSE.values()}


def select_playbook(root_cause: RootCause) -> Playbook | None:
    """None means: recoverable by nobody write off without contact."""
    return _BY_CAUSE.get(root_cause)
