"""Measured money recovered across the batch the numbers behind the bar."""

from __future__ import annotations

from collections import defaultdict

from .models import Case, CaseStatus, LeakType

LEAK_LABELS = {
    LeakType.PAYMENT_FAILURE: "Payment failures",
    LeakType.CHECKOUT_ABANDONMENT: "Checkout abandonment",
    LeakType.SUBSCRIPTION_RENEWAL_FAILURE: "Subscription renewals",
    LeakType.INVOICE_OVERDUE: "B2B receivables",
}


def summarise(cases: list[Case], guardrail_events: dict[str, int],
              paused_channels: list[str]) -> dict:
    total_at_risk = sum(c.amount_paise for c in cases)
    recovered = sum(c.recovered_paise for c in cases)
    spend = sum(c.cost_paise for c in cases)
    discounts = sum(c.discount_paise for c in cases)

    by_leak: dict[str, dict] = {}
    for leak in LeakType:
        subset = [c for c in cases if c.leak_type == leak]
        if not subset:
            continue
        rec = [c for c in subset if c.status == CaseStatus.RECOVERED]
        at_risk = sum(c.amount_paise for c in subset)
        got = sum(c.recovered_paise for c in subset)
        by_leak[leak.value] = {
            "label": LEAK_LABELS[leak],
            "cases": len(subset),
            "recovered_cases": len(rec),
            "at_risk_paise": at_risk,
            "recovered_paise": got,
            "rate_pct": round(100 * got / at_risk, 1) if at_risk else 0.0,
        }

    by_status = defaultdict(int)
    for c in cases:
        by_status[c.status.value] += 1

    by_channel: dict[str, dict] = {}
    for c in cases:
        for a in c.attempts:
            row = by_channel.setdefault(
                a.channel.value, {"attempts": 0, "cost_paise": 0})
            row["attempts"] += 1
            row["cost_paise"] += a.cost_paise

    recovered_cases = [c for c in cases if c.status == CaseStatus.RECOVERED]
    hours = [
        (c.recovered_at - c.created_at).total_seconds() / 3600
        for c in recovered_cases if c.recovered_at
    ]
    promises = [c for c in cases if c.promise is not None]

    return {
        "cases": len(cases),
        "at_risk_paise": total_at_risk,
        "recovered_paise": recovered,
        "recovery_rate_pct": round(100 * recovered / total_at_risk, 1)
        if total_at_risk else 0.0,
        "recovered_cases": len(recovered_cases),
        "spend_paise": spend,
        "discounts_paise": discounts,
        "net_paise": recovered - spend,
        "roi": round(recovered / spend, 1) if spend else 0.0,
        "avg_hours_to_recovery": round(sum(hours) / len(hours), 1) if hours else 0.0,
        "promises_made": len(promises),
        "promises_kept": sum(1 for c in promises if c.promise.kept is True),
        "promises_broken": sum(1 for c in promises if c.promise.kept is False),
        "by_status": dict(by_status),
        "by_leak": by_leak,
        "by_channel": by_channel,
        "guardrail_events": dict(sorted(guardrail_events.items(),
                                        key=lambda kv: -kv[1])),
        "paused_channels": paused_channels,
    }
