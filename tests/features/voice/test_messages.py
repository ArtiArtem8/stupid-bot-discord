"""Exercise Russian plural forms and typed profile templates through gettext."""

from datetime import date
from unittest import TestCase

from stupidbot.features.voice.messages import VoiceMessages
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language


class VoiceMessagesTests(TestCase):
    def test_korean_dates_and_people_counts(self) -> None:
        korean = VoiceMessages(catalog("voice", Language.KO))
        self.assertEqual(korean.date_label(date(2026, 9, 21)), "2026년 9월 21일")
        self.assertEqual(korean.duration(3660), "1시간 01분")
        for count in (0, 1, 2, 5, 21, 1000):
            with self.subTest(count=count):
                self.assertEqual(
                    korean.recent_people(duration="1시간", count=count),
                    f"1시간 함께 · {count:,}명",
                )

    def test_russian_and_english_people_plural_forms(self) -> None:
        russian = VoiceMessages(catalog("voice", Language.RU))
        english = VoiceMessages(catalog("voice", Language.EN))
        for count in (0, 1, 2, 5, 11, 21, 22, 25, 101):
            with self.subTest(count=count):
                noun = "человека" if count in (2, 22) else "человек"
                self.assertEqual(
                    russian.recent_people(duration="1ч", count=count),
                    f"1ч вместе · {count} {noun}",
                )
                self.assertEqual(
                    english.recent_people(duration="1h", count=count),
                    f"1h together · {count} {'person' if count == 1 else 'people'}",
                )

    def test_dates_and_elapsed_duration_do_not_depend_on_process_locale(self) -> None:
        russian = VoiceMessages(catalog("voice", Language.RU))
        english = VoiceMessages(catalog("voice", Language.EN))
        self.assertEqual(russian.date_label(date(2026, 9, 21)), "21 сен. 2026")
        self.assertEqual(english.date_label(date(2026, 9, 21)), "Sep 21, 2026")
        self.assertEqual(russian.duration(3660), "1ч 01м")
        self.assertEqual(english.duration(3660), "1h 01m")
