"""Guardrails are the product. Each rule gets a direct unit test."""

import unittest
from datetime import datetime, timedelta

from engine.config import Policy
from engine.guardrails import Guardrails
from engine.models import (
    ActionKind, Attempt, Case, Channel, Customer, Diagnosis, LeakType,
    Outcome, RootCause,
)
from engine.playbooks import Step

DAY = datetime(2026, 8, 3, 12, 0)   # 12:00, well inside contact hours
NIGHT = datetime(2026, 8, 3, 22, 30)


def make_case(amount_paise=500_000, leak=LeakType.PAYMENT_FAILURE,
              cause=RootCause.INSUFFICIENT_FUNDS, consented=None) -> Case:
    customer = Customer(
        id="cust_x", name="Test Payer", segment="b2c",
        consented_channels=consented if consented is not None else {
            Channel.EMAIL, Channel.SMS, Channel.WHATSAPP, Channel.VOICE_HINGLISH,
        },
        salary_day=1,
    )
    case = Case(id="case_x", leak_type=leak, amount_paise=amount_paise,
                customer=customer, created_at=DAY)
    case.diagnosis = Diagnosis(cause, 0.9, "test")
    return case


class GuardrailsTest(unittest.TestCase):
    def setUp(self):
        self.policy = Policy()
        self.g = Guardrails(self.policy)

    def test_quiet_hours_defer_contact(self):
        case = make_case()
        step = Step(ActionKind.NUDGE, Channel.WHATSAPP, "+1h")
        v = self.g.check(case, step, NIGHT, recent_contacts=0)
        self.assertEqual(v.action, "defer")
        self.assertEqual(v.rule, "quiet_hours")
        self.assertEqual(v.defer_until.hour, self.policy.quiet_hours_end)
        self.assertGreater(v.defer_until, NIGHT)

    def test_quiet_hours_do_not_block_silent_retry(self):
        case = make_case()
        step = Step(ActionKind.RETRY, Channel.SMART_RETRY, "+1h")
        v = self.g.check(case, step, NIGHT, recent_contacts=0)
        self.assertEqual(v.action, "proceed")

    def test_no_consent_skips_channel(self):
        case = make_case(consented={Channel.EMAIL})
        step = Step(ActionKind.NUDGE, Channel.WHATSAPP, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "no_consent"))

    def test_weekly_contact_cap_defers(self):
        case = make_case()
        step = Step(ActionKind.NUDGE, Channel.EMAIL, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=3)
        self.assertEqual((v.action, v.rule), ("defer", "weekly_contact_cap"))

    def test_voice_window(self):
        case = make_case()
        step = Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+1h")
        early = DAY.replace(hour=8, minute=30)
        v = self.g.check(case, step, early, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("defer", "voice_window"))
        self.assertEqual(v.defer_until.hour, self.policy.voice_hours_start)

    def test_mandate_retry_requires_predebit_notice(self):
        case = make_case(leak=LeakType.SUBSCRIPTION_RENEWAL_FAILURE,
                         cause=RootCause.MANDATE_PAUSED)
        step = Step(ActionKind.RETRY, Channel.SMART_RETRY, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "predebit_missing"))

        case.flags["predebit_at"] = DAY - timedelta(hours=6)   # too fresh
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("defer", "predebit_cooling"))
        self.assertEqual(v.defer_until,
                         case.flags["predebit_at"] + timedelta(hours=24))

        case.flags["predebit_at"] = DAY - timedelta(hours=30)  # in window
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual(v.action, "proceed")

        case.flags["predebit_at"] = DAY - timedelta(hours=100)  # stale
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "predebit_stale"))

    def test_small_ticket_blocks_expensive_channels(self):
        case = make_case(amount_paise=19_900)  # ₹199 subscription
        voice = Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+1h")
        v = self.g.check(case, voice, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "small_ticket"))
        email = Step(ActionKind.NUDGE, Channel.EMAIL, "+1h")
        v = self.g.check(case, email, DAY, recent_contacts=0)
        self.assertEqual(v.action, "proceed")

    def test_negative_expected_value_stops_spend(self):
        # ₹260 cart, two contacts already burned: expected recovery of a ₹12
        # voice call is below its cost, so the agent must not make it.
        case = make_case(amount_paise=26_000, leak=LeakType.CHECKOUT_ABANDONMENT,
                         cause=RootCause.PRICE_HESITATION)
        case.diagnosis = Diagnosis(RootCause.PRICE_HESITATION, 0.78, "test")
        for i in range(2):
            case.attempts.append(Attempt(i, ActionKind.NUDGE, Channel.EMAIL,
                                         DAY, 10, Outcome.NO_RESPONSE))
        step = Step(ActionKind.NUDGE, Channel.VOICE_HINGLISH, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "negative_expected_value"))

    def test_attempt_cap_stops_case(self):
        case = make_case()
        for i in range(self.policy.max_attempts_per_case):
            case.attempts.append(Attempt(i, ActionKind.RETRY, Channel.SMART_RETRY,
                                         DAY, 300, Outcome.NO_RESPONSE))
        step = Step(ActionKind.RETRY, Channel.SMART_RETRY, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("stop", "attempt_cap"))

    def test_offer_is_clamped_to_policy_ceiling(self):
        case = make_case(leak=LeakType.CHECKOUT_ABANDONMENT,
                         cause=RootCause.PRICE_HESITATION)
        case.diagnosis = Diagnosis(RootCause.PRICE_HESITATION, 0.78, "test")
        step = Step(ActionKind.NUDGE, Channel.EMAIL, "+1h", offer_bps=2_000)
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual(v.action, "proceed")
        self.assertEqual(v.offer_bps, self.policy.max_discount_bps)

    def test_frozen_case_is_untouchable(self):
        case = make_case()
        case.frozen = True
        step = Step(ActionKind.NUDGE, Channel.EMAIL, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("stop", "dispute_freeze"))

    def test_circuit_breaker_trips_and_pauses(self):
        tripped = self.g.observe_channel(Channel.SMS, attempts=40, successes=0)
        self.assertTrue(tripped)
        case = make_case()
        step = Step(ActionKind.PAYMENT_LINK, Channel.SMS, "+1h")
        v = self.g.check(case, step, DAY, recent_contacts=0)
        self.assertEqual((v.action, v.rule), ("skip", "channel_breaker"))


if __name__ == "__main__":
    unittest.main()
