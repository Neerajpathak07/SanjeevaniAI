"""Domain model: leaks, diagnoses, cases, attempts and money.

All money is held as integer paise. Floating point never touches a rupee.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


def fmt_inr(paise: int) -> str:
    """Format paise as rupees with Indian digit grouping (₹12,34,567.50)."""
    sign = "-" if paise < 0 else ""
    paise = abs(paise)
    rupees, p = divmod(paise, 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    if p:
        return f"{sign}₹{s}.{p:02d}"
    return f"{sign}₹{s}"


class LeakType(str, enum.Enum):
    PAYMENT_FAILURE = "payment_failure"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    SUBSCRIPTION_RENEWAL_FAILURE = "subscription_renewal_failure"
    INVOICE_OVERDUE = "invoice_overdue"


class RootCause(str, enum.Enum):
    ISSUER_DOWNTIME = "issuer_downtime"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_INSTRUMENT = "expired_instrument"
    OTP_TIMEOUT = "otp_timeout"
    PRICE_HESITATION = "price_hesitation"
    MANDATE_PAUSED = "mandate_paused"
    INVOICE_LOST_IN_APPROVAL = "invoice_lost_in_approval"
    INVOICE_DISPUTED = "invoice_disputed"
    RISK_DECLINE = "risk_decline"


class Channel(str, enum.Enum):
    SMART_RETRY = "smart_retry"        # gateway-side, not a customer contact
    EMAIL = "email"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    VOICE_HINGLISH = "voice_hinglish"
    HUMAN_AGENT = "human_agent"


CONTACT_CHANNELS = {
    Channel.EMAIL, Channel.SMS, Channel.WHATSAPP, Channel.VOICE_HINGLISH,
}


class ActionKind(str, enum.Enum):
    RETRY = "retry"                    # re-attempt the charge
    NUDGE = "nudge"                    # reminder / winback message
    PAYMENT_LINK = "payment_link"      # message carrying a fresh payment link
    PRE_DEBIT_NOTICE = "pre_debit_notice"  # RBI-mandated notice before a mandate debit
    PROMISE_CHECK = "promise_check"    # verify a promise-to-pay on its due date
    ESCALATE_HUMAN = "escalate_human"  # hand the case to a human, agent stops


class CaseStatus(str, enum.Enum):
    DETECTED = "detected"
    IN_RECOVERY = "in_recovery"
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    STOPPED = "stopped"
    WRITTEN_OFF = "written_off"


class Outcome(str, enum.Enum):
    PAID = "paid"
    NO_RESPONSE = "no_response"
    PROMISE_MADE = "promise_made"
    PROMISE_KEPT = "promise_kept"
    PROMISE_BROKEN = "promise_broken"
    DISPUTE_RAISED = "dispute_raised"
    NOTICE_DELIVERED = "notice_delivered"
    HANDED_TO_HUMAN = "handed_to_human"


@dataclass
class Customer:
    id: str
    name: str
    segment: str                      # "b2c" | "b2b"
    consented_channels: set[Channel]
    salary_day: int                   # day-of-month salary usually lands
    language: str = "hinglish"        # preferred voice language

    def consents_to(self, channel: Channel) -> bool:
        if channel in (Channel.SMART_RETRY, Channel.HUMAN_AGENT):
            return True
        return channel in self.consented_channels


@dataclass
class Diagnosis:
    root_cause: RootCause
    confidence: float
    narrative: str
    recoverable: bool = True


@dataclass
class TriageScore:
    expected_value_paise: int
    priority: str                     # "P0" .. "P3"


@dataclass
class Promise:
    made_at: datetime
    due_at: datetime
    amount_paise: int
    kept: Optional[bool] = None


@dataclass
class Attempt:
    step_index: int
    kind: ActionKind
    channel: Channel
    at: datetime
    cost_paise: int
    outcome: Outcome
    detail: str = ""


@dataclass
class Case:
    id: str
    leak_type: LeakType
    amount_paise: int
    customer: Customer
    created_at: datetime
    signals: dict = field(default_factory=dict)
    status: CaseStatus = CaseStatus.DETECTED
    diagnosis: Optional[Diagnosis] = None
    triage: Optional[TriageScore] = None
    playbook: Optional[str] = None
    attempts: list[Attempt] = field(default_factory=list)
    promise: Optional[Promise] = None
    cost_paise: int = 0
    discount_paise: int = 0
    recovered_paise: int = 0
    recovered_at: Optional[datetime] = None
    stop_reason: Optional[str] = None
    frozen: bool = False              # set on dispute no further outreach
    flags: dict = field(default_factory=dict)

    @property
    def contacts_made(self) -> int:
        return sum(1 for a in self.attempts if a.channel in CONTACT_CHANNELS)

    @property
    def terminal(self) -> bool:
        return self.status in (
            CaseStatus.RECOVERED, CaseStatus.ESCALATED,
            CaseStatus.STOPPED, CaseStatus.WRITTEN_OFF,
        )
