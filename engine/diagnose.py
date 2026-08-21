"""Diagnosis: observable signals -> root cause, with confidence.

A deterministic rules engine the same signal always yields the same
diagnosis, which is what an auditor wants. The optional LLM layer (brain.py)
only writes prose on top; it never changes the decision.
"""

from __future__ import annotations

from .models import Case, Diagnosis, LeakType, RootCause


def diagnose(case: Case) -> Diagnosis:
    s = case.signals
    code = s.get("error_code", "")

    if code == "RISK_59_SUSPECTED_FRAUD":
        return Diagnosis(
            RootCause.RISK_DECLINE, 0.97,
            "Risk engine declined the payment. Chasing a risk-declined payer "
            "is both non-compliant and pointless write off, never contact.",
            recoverable=False,
        )
    if code == "GW_91_ISSUER_UNAVAILABLE":
        return Diagnosis(
            RootCause.ISSUER_DOWNTIME, 0.95,
            "Issuer bank was unreachable at charge time. Money is willing; "
            "the pipe was down. Retry when the bank wakes up.",
        )
    if code == "BANK_51_INSUFFICIENT_FUNDS":
        return Diagnosis(
            RootCause.INSUFFICIENT_FUNDS, 0.93,
            "Account balance too low at charge time. Best cure: retry in the "
            f"salary window (payer's salary usually lands on day "
            f"{case.customer.salary_day}).",
        )
    if code == "BANK_54_EXPIRED_CARD":
        return Diagnosis(
            RootCause.EXPIRED_INSTRUMENT, 0.96,
            "Card on file has expired. No retry can fix plastic send a "
            "payment link to capture a fresh instrument.",
        )
    if code == "3DS_OTP_TIMEOUT":
        return Diagnosis(
            RootCause.OTP_TIMEOUT, 0.90,
            "Payer began 3DS but the OTP step timed out intent was real, "
            "friction won. A one-tap payment link usually closes it.",
        )
    if code == "MANDATE_PAUSED_BY_PAYER":
        return Diagnosis(
            RootCause.MANDATE_PAUSED, 0.92,
            "e-Mandate paused by the payer. Requires a fresh RBI pre-debit "
            "notice ≥24h before any re-presentation.",
        )

    if case.leak_type == LeakType.CHECKOUT_ABANDONMENT:
        stage = s.get("drop_stage", "")
        if stage == "order_review":
            return Diagnosis(
                RootCause.PRICE_HESITATION, 0.78,
                f"Cart abandoned at order review after "
                f"{s.get('dwell_seconds', '?')}s of dwell classic sticker "
                "shock. A gentle nudge (and at most a bounded offer) applies.",
            )
        return Diagnosis(
            RootCause.OTP_TIMEOUT, 0.60,
            "Dropped inside the payment step; treating as payment friction.",
        )

    if case.leak_type == LeakType.INVOICE_OVERDUE:
        if "buyer_note" in s:
            return Diagnosis(
                RootCause.INVOICE_DISPUTED, 0.85,
                f"Buyer flagged: “{s['buyer_note']}”. Disputed receivables "
                "must never enter a dunning sequence freeze and hand to a "
                "human immediately.",
            )
        return Diagnosis(
            RootCause.INVOICE_LOST_IN_APPROVAL, 0.80,
            f"Invoice {s.get('days_overdue', '?')} days overdue with no "
            "dispute on record most likely stuck in the buyer's approval "
            "chain. Ladder: remind -> statement -> call -> promise-to-pay.",
        )

    return Diagnosis(
        RootCause.OTP_TIMEOUT, 0.40,
        "Ambiguous signals; defaulting to low-friction payment-link recovery.",
    )
