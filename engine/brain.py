"""Message drafting deterministic templates, optionally polished by Claude.

The decision layer (diagnose / guardrails / playbooks) is fully rule-based
and never depends on an LLM: the agent's *choices* are reproducible and
auditable. The LLM only writes better prose for the messages we were going
to send anyway.

If `ANTHROPIC_API_KEY` is set and the `anthropic` package is installed, copy
is drafted by Claude (model `claude-opus-4-8`, adaptive thinking) and cached
per template so a 500-case batch costs a handful of calls. In every other
situation no key, no package, network error the engine falls back to the
built-in templates and the demo runs fully offline.
"""

from __future__ import annotations

import os

from .models import ActionKind, Case, Channel, fmt_inr
from .playbooks import Step

_TEMPLATES: dict[tuple[str, str], str] = {
    # (kind, channel) -> template. {name}, {amount}, {offer} interpolated.
    ("nudge", "email"): (
        "Hi {name}, your payment of {amount} didn't go through no charge "
        "was made. Your order/plan is saved. Complete it anytime here: <link>."
    ),
    ("nudge", "whatsapp"): (
        "Hi {name} aapka {amount} ka payment atak gaya tha koi charge "
        "nahi hua hai. Cart/plan safe hai. Ek tap mein complete karein: <link>{offer}"
    ),
    ("nudge", "voice_hinglish"): (
        "Namaste {name} ji, main Sanjeevani bol rahi hoon. Aapka {amount} ka "
        "payment complete nahi ho paya tha. Koi jaldi nahi hai jab aap "
        "chahein, hum abhi SMS par ek secure link bhej rahe hain. Shukriya!"
    ),
    ("payment_link", "email"): (
        "Hi {name}, here's a fresh secure link to complete your {amount} "
        "payment (your earlier attempt failed through no fault of yours): <link>."
    ),
    ("payment_link", "sms"): (
        "{name}, complete your {amount} payment securely: <link>. "
        "Reply STOP to opt out."
    ),
    ("payment_link", "whatsapp"): (
        "Hi {name}! Payment poora karne ke liye bas ek tap: <link> "
        "({amount}). OTP ki jhanjhat nahi saved details se turant ho jayega."
    ),
    ("pre_debit_notice", "sms"): (
        "Notice: {amount} will be debited from your account via your "
        "registered e-mandate after 24 hours. To pause or cancel, tap: <link>."
    ),
    ("nudge_b2b", "email"): (
        "Dear {name}, invoice <inv> for {amount} is past due. A copy and a "
        "payment link are attached. If it's already in process, ignore this."
    ),
    ("nudge_b2b", "voice_hinglish"): (
        "Namaste, {name} ke accounts team se baat ho sakti hai? Invoice "
        "<inv>, amount {amount}, kaafi time se pending hai. Kya aap payment "
        "date confirm kar sakte hain? Hum wahi date note kar lenge."
    ),
}


def _template_key(case: Case, step: Step) -> tuple[str, str]:
    kind = step.kind.value
    if case.customer.segment == "b2b" and kind in ("nudge", "payment_link"):
        kind = "nudge_b2b"
        if step.channel == Channel.EMAIL:
            return (kind, "email")
        return (kind, "voice_hinglish")
    return (kind, step.channel.value)


class Narrator:
    """Drafts outbound copy. LLM-optional, template-guaranteed."""

    def __init__(self, use_llm: bool | None = None):
        self._cache: dict[tuple, str] = {}
        self._client = None
        if use_llm is False:
            return
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic  # optional dependency
                self._client = anthropic.Anthropic()
            except Exception:
                self._client = None

    @property
    def llm_active(self) -> bool:
        return self._client is not None

    def draft(self, case: Case, step: Step, offer_bps: int = 0) -> str:
        key = _template_key(case, step)
        base = _TEMPLATES.get(key)
        if base is None:
            return ""
        offer_txt = ""
        if offer_bps:
            offer_txt = f" (extra {offer_bps // 100}% off if completed today 🎁)"
        rendered = base.format(
            name=case.customer.name.split()[0],
            amount=fmt_inr(case.amount_paise),
            offer=offer_txt,
        )
        if self._client is None:
            return rendered
        cache_key = (key, case.customer.language, bool(offer_bps))
        if cache_key in self._cache:
            return self._cache[cache_key].format(
                name=case.customer.name.split()[0],
                amount=fmt_inr(case.amount_paise), offer=offer_txt)
        polished = self._polish(rendered, step, case.customer.language)
        # Cache the polished text as a template with placeholders restored
        self._cache[cache_key] = polished.replace(
            case.customer.name.split()[0], "{name}").replace(
            fmt_inr(case.amount_paise), "{amount}") + ("{offer}" if offer_bps else "")
        return polished

    def _polish(self, draft: str, step: Step, language: str) -> str:
        try:
            resp = self._client.messages.create(
                model="claude-opus-4-8",
                max_tokens=300,
                thinking={"type": "adaptive"},
                system=(
                    "You polish payment-recovery messages for an Indian "
                    "audience. Keep the same meaning, links and amounts as "
                    "placeholders, stay under 320 characters, warm and "
                    "non-pushy, never threatening. "
                    f"Preferred language style: {language}. "
                    "Return only the message text."
                ),
                messages=[{"role": "user", "content": draft}],
            )
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    return block.text.strip()
            return draft
        except Exception:
            return draft
