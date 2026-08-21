"""Triage: how urgently is this case worth working?

Expected value = amount x prior probability of first-line success. Priority
bands decide console ordering and (in a real deployment) queue position
they never override guardrails.
"""

from __future__ import annotations

from .config import prior_success
from .models import Case, Channel, TriageScore

_FIRST_LINE_KIND = {
    "issuer_downtime": "retry",
    "insufficient_funds": "retry",
    "expired_instrument": "payment_link",
    "otp_timeout": "payment_link",
    "price_hesitation": "nudge",
    "mandate_paused": "retry",
    "invoice_lost_in_approval": "voice",
    "invoice_disputed": "nudge",
    "risk_decline": "nudge",
}


def triage(case: Case) -> TriageScore:
    assert case.diagnosis is not None
    cause = case.diagnosis.root_cause.value
    kind = _FIRST_LINE_KIND.get(cause, "nudge")
    channel = Channel.VOICE_HINGLISH if kind == "voice" else Channel.EMAIL
    p = prior_success(cause, kind, channel) * case.diagnosis.confidence
    ev = int(case.amount_paise * p)
    if not case.diagnosis.recoverable:
        priority = "P3"
    elif ev >= 5_000_00:        # ≥ ₹5,000 expected
        priority = "P0"
    elif ev >= 500_00:          # ≥ ₹500
        priority = "P1"
    elif ev >= 50_00:           # ≥ ₹50
        priority = "P2"
    else:
        priority = "P3"
    return TriageScore(expected_value_paise=ev, priority=priority)
