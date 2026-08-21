"""Seeded synthetic batch generator.

Produces a batch of at-risk revenue events with *observable* signals (gateway
error codes, funnel stages, invoice ages) derived from a *hidden* ground
truth that only the simulation world sees. The agent must work from the
signals, exactly as it would in production.

Everything is driven by one seed the same seed always produces the same
batch, the same customers and the same hidden temperaments, which is what
makes recovery numbers reproducible and auditable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import Case, Channel, Customer, LeakType, RootCause

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Ananya", "Diya", "Ishaan", "Kavya", "Rohan",
    "Sneha", "Arjun", "Meera", "Kabir", "Priya", "Nikhil", "Sanya", "Farhan",
    "Zoya", "Dev", "Tara", "Manav",
]
LAST_NAMES = [
    "Sharma", "Verma", "Iyer", "Patel", "Reddy", "Nair", "Gupta", "Khan",
    "Deshpande", "Chatterjee", "Menon", "Joshi", "Kulkarni", "Bose", "Rao",
]
B2B_NAMES = [
    "Chai Point Retail Pvt Ltd", "Nimbus Logistics LLP", "Kirana Konnect",
    "Vistara Textiles", "Bluegrass Media Works", "Apex Tooling Co",
    "Saffron Foods Distribution", "Trellis Software Labs", "Meridian Pharma",
    "Havelock Traders", "Udaan Freight Services", "Lotus Print House",
]

#: hidden root-cause mix per leak type
CAUSE_MIX: dict[LeakType, list[tuple[RootCause, float]]] = {
    LeakType.PAYMENT_FAILURE: [
        (RootCause.INSUFFICIENT_FUNDS, 0.38),
        (RootCause.ISSUER_DOWNTIME, 0.24),
        (RootCause.EXPIRED_INSTRUMENT, 0.18),
        (RootCause.OTP_TIMEOUT, 0.12),
        (RootCause.RISK_DECLINE, 0.08),
    ],
    LeakType.CHECKOUT_ABANDONMENT: [
        (RootCause.PRICE_HESITATION, 0.62),
        (RootCause.OTP_TIMEOUT, 0.28),
        (RootCause.ISSUER_DOWNTIME, 0.10),
    ],
    LeakType.SUBSCRIPTION_RENEWAL_FAILURE: [
        (RootCause.INSUFFICIENT_FUNDS, 0.40),
        (RootCause.MANDATE_PAUSED, 0.35),
        (RootCause.EXPIRED_INSTRUMENT, 0.25),
    ],
    LeakType.INVOICE_OVERDUE: [
        (RootCause.INVOICE_LOST_IN_APPROVAL, 0.90),
        (RootCause.INVOICE_DISPUTED, 0.10),
    ],
}

LEAK_MIX = [
    (LeakType.PAYMENT_FAILURE, 0.34),
    (LeakType.CHECKOUT_ABANDONMENT, 0.30),
    (LeakType.SUBSCRIPTION_RENEWAL_FAILURE, 0.22),
    (LeakType.INVOICE_OVERDUE, 0.14),
]

#: observable gateway error codes emitted per hidden cause
ERROR_CODES = {
    RootCause.INSUFFICIENT_FUNDS: "BANK_51_INSUFFICIENT_FUNDS",
    RootCause.ISSUER_DOWNTIME: "GW_91_ISSUER_UNAVAILABLE",
    RootCause.EXPIRED_INSTRUMENT: "BANK_54_EXPIRED_CARD",
    RootCause.OTP_TIMEOUT: "3DS_OTP_TIMEOUT",
    RootCause.RISK_DECLINE: "RISK_59_SUSPECTED_FRAUD",
    RootCause.MANDATE_PAUSED: "MANDATE_PAUSED_BY_PAYER",
}


@dataclass
class CaseTruth:
    """Hidden per-case ground truth visible only to the simulation world."""
    root_cause: RootCause
    responsiveness: float      # 0.5 (ghost) … 1.6 (eager)
    promise_keeper: bool       # honours a promise-to-pay
    will_dispute: bool         # raises a dispute if chased


def _weighted(rng: random.Random, options: list[tuple]) -> object:
    r = rng.random()
    acc = 0.0
    for value, weight in options:
        acc += weight
        if r <= acc:
            return value
    return options[-1][0]


def _customer(rng: random.Random, idx: int, b2b: bool) -> Customer:
    if b2b:
        name = f"{rng.choice(B2B_NAMES)}"
        consented = {Channel.EMAIL, Channel.VOICE_HINGLISH}
        if rng.random() < 0.6:
            consented.add(Channel.WHATSAPP)
    else:
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        consented = {Channel.SMS}
        if rng.random() < 0.9:
            consented.add(Channel.EMAIL)
        if rng.random() < 0.75:
            consented.add(Channel.WHATSAPP)
        if rng.random() < 0.5:
            consented.add(Channel.VOICE_HINGLISH)
    return Customer(
        id=f"cust_{idx:05d}",
        name=name,
        segment="b2b" if b2b else "b2c",
        consented_channels=consented,
        salary_day=rng.choice([1, 1, 5, 7, 10, 15, 25, 28, 30]),
        language=rng.choice(["hinglish", "hinglish", "english"]),
    )


def _amount(rng: random.Random, leak: LeakType) -> int:
    """Amount in paise, shaped per leak type."""
    if leak == LeakType.CHECKOUT_ABANDONMENT:
        return rng.randint(99, 9_999) * 100
    if leak == LeakType.PAYMENT_FAILURE:
        return rng.randint(299, 24_999) * 100
    if leak == LeakType.SUBSCRIPTION_RENEWAL_FAILURE:
        return rng.choice([19_900, 29_900, 49_900, 99_900, 149_900, 199_900])
    # B2B invoices: ₹40k – ₹12L
    return rng.randint(40_000, 1_200_000) * 100


def _signals(rng: random.Random, leak: LeakType, cause: RootCause,
             created_at: datetime) -> dict:
    s: dict = {"leak_type": leak.value}
    if leak == LeakType.CHECKOUT_ABANDONMENT:
        if cause == RootCause.PRICE_HESITATION:
            s["drop_stage"] = "order_review"
            s["dwell_seconds"] = rng.randint(40, 300)
        elif cause == RootCause.OTP_TIMEOUT:
            s["drop_stage"] = "otp_screen"
            s["error_code"] = ERROR_CODES[cause]
        else:
            s["drop_stage"] = "payment_processing"
            s["error_code"] = ERROR_CODES[cause]
    elif leak == LeakType.INVOICE_OVERDUE:
        s["days_overdue"] = rng.randint(12, 60)
        s["reminders_ignored"] = rng.randint(0, 2)
        if cause == RootCause.INVOICE_DISPUTED:
            s["buyer_note"] = "quantity mismatch on line items"
    else:
        s["error_code"] = ERROR_CODES.get(cause, "GW_05_DO_NOT_HONOUR")
        s["attempt_no"] = 1
        if leak == LeakType.SUBSCRIPTION_RENEWAL_FAILURE:
            s["mandate_id"] = f"mand_{rng.randint(10**9, 10**10 - 1)}"
    return s


def generate_batch(n_cases: int, seed: int, start: datetime,
                   ) -> tuple[list[Case], dict[str, CaseTruth]]:
    """Return (cases, hidden truths keyed by case id)."""
    rng = random.Random(seed)
    cases: list[Case] = []
    truths: dict[str, CaseTruth] = {}
    for i in range(n_cases):
        leak = _weighted(rng, LEAK_MIX)
        cause = _weighted(rng, CAUSE_MIX[leak])
        b2b = leak == LeakType.INVOICE_OVERDUE
        customer = _customer(rng, i, b2b)
        created_at = start + timedelta(minutes=rng.randint(0, 36 * 60))
        case = Case(
            id=f"case_{i:05d}",
            leak_type=leak,
            amount_paise=_amount(rng, leak),
            customer=customer,
            created_at=created_at,
            signals=_signals(rng, leak, cause, created_at),
        )
        cases.append(case)
        truths[case.id] = CaseTruth(
            root_cause=cause,
            responsiveness=round(0.5 + rng.random() * 1.1, 3),
            promise_keeper=rng.random() < 0.72,
            will_dispute=(cause == RootCause.INVOICE_DISPUTED),
        )
    return cases, truths
