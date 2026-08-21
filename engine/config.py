"""Policy-as-code.

Every knob the recovery agent obeys lives here, in one dataclass, and a
snapshot of it is written into the audit ledger at the start of every run
so an auditor can always answer "under which rules did the agent act?".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from .models import Channel


def _default_action_costs() -> dict:
    # paise per action what one attempt on this channel costs us
    return {
        Channel.SMART_RETRY.value: 300,       # ₹3    gateway retry fee
        Channel.EMAIL.value: 10,              # ₹0.10
        Channel.SMS.value: 20,                # ₹0.20
        Channel.WHATSAPP.value: 80,           # ₹0.80
        Channel.VOICE_HINGLISH.value: 1200,   # ₹12   outbound voice-bot call
        Channel.HUMAN_AGENT.value: 15000,     # ₹150  human collections touch
    }


@dataclass
class Policy:
    version: str = "1.0.0"

    # --- Compliance -------------------------------------------------------
    quiet_hours_start: int = 21     # no customer contact 21:00 -> 08:00 IST
    quiet_hours_end: int = 8
    voice_hours_start: int = 10     # voice calls only 10:00 -> 19:00 IST
    voice_hours_end: int = 19
    # RBI e-mandate rule: a pre-debit notification must precede a mandate
    # retry by at least `predebit_min_hours`, and goes stale after
    # `predebit_max_hours` (then a fresh notice is required).
    predebit_min_hours: int = 24
    predebit_max_hours: int = 72
    require_channel_consent: bool = True
    freeze_on_dispute: bool = True  # a raised dispute halts all outreach

    # --- Contact hygiene --------------------------------------------------
    max_contacts_per_case: int = 4          # customer-facing messages/calls
    max_contacts_per_week: int = 3          # per customer, across all cases
    max_attempts_per_case: int = 8          # everything, retries included
    max_defers_per_step: int = 3            # then the step is skipped

    # --- Stopping rules (economics) ---------------------------------------
    # Never spend more on an action than its expected recovery:
    # blocked when action_cost > p_est * amount.
    expected_value_stop: bool = True
    # Below this amount, only near-free channels are worth using.
    small_ticket_paise: int = 25_000        # ₹250
    small_ticket_max_action_cost: int = 100  # ₹1
    # Attempt-fatigue decay applied to prior success estimates per contact.
    fatigue_decay: float = 0.70

    # --- Offers -----------------------------------------------------------
    max_discount_bps: int = 700             # a winback offer may never exceed 7%

    # --- Circuit breaker --------------------------------------------------
    breaker_min_attempts: int = 40          # per channel, before it can trip
    breaker_min_success_rate: float = 0.02  # below this the channel is paused

    # --- Run bounds -------------------------------------------------------
    horizon_days: int = 14                  # the workflow is time-bounded

    action_costs: dict = field(default_factory=_default_action_costs)

    def action_cost(self, channel: Channel) -> int:
        return int(self.action_costs[channel.value])

    def to_dict(self) -> dict:
        return asdict(self)


DEFAULT_POLICY = Policy()


# ---------------------------------------------------------------------------
# Prior success-rate estimates, keyed (root_cause, action_kind).
#
# These priors serve two masters, deliberately from the same table:
#   * guardrails use them to compute the expected value of an action before
#     spending money on it (the stopping rule),
#   * the simulation world uses them (modulated by hidden per-customer
#     factors) to decide what actually happens.
# ---------------------------------------------------------------------------

PRIORS: dict[tuple[str, str], float] = {
    ("issuer_downtime", "retry"): 0.55,
    ("issuer_downtime", "nudge"): 0.15,
    ("insufficient_funds", "retry"): 0.16,   # ×~2.5 in a salary-day window
    ("insufficient_funds", "nudge"): 0.08,
    ("insufficient_funds", "payment_link"): 0.10,
    ("expired_instrument", "payment_link"): 0.30,
    ("expired_instrument", "nudge"): 0.12,
    ("otp_timeout", "payment_link"): 0.35,
    ("otp_timeout", "nudge"): 0.15,
    ("price_hesitation", "nudge"): 0.10,     # ×2 when a bounded offer rides along
    ("price_hesitation", "payment_link"): 0.14,
    ("mandate_paused", "retry"): 0.35,       # only after a valid pre-debit notice
    ("mandate_paused", "nudge"): 0.10,
    ("mandate_paused", "pre_debit_notice"): 1.0,   # delivery, not payment
    ("invoice_lost_in_approval", "nudge"): 0.12,
    ("invoice_lost_in_approval", "payment_link"): 0.15,
    ("invoice_lost_in_approval", "voice"): 0.45,   # usually yields a promise
}

SALARY_WINDOW_MULTIPLIER = 2.5   # NSF retry timed to payday
OFFER_MULTIPLIER = 2.0           # winback nudge carrying a discount


def prior_success(root_cause: str, kind: str, channel: Channel) -> float:
    """Best-effort prior for an action. Voice gets its own row where present."""
    if channel == Channel.VOICE_HINGLISH and (root_cause, "voice") in PRIORS:
        return PRIORS[(root_cause, "voice")]
    return PRIORS.get((root_cause, kind), 0.05)
