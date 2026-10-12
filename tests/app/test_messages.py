"""Localized presence keeps the established monotonic-duration presentation."""

import unittest

from stupidbot.app.messages import AppMessages
from stupidbot.common.duration import TimeUnit, format_duration_ru
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language


class TestAppMessages(unittest.TestCase):
    def test_russian_presence_preserves_existing_duration_output(self) -> None:
        messages = AppMessages(catalog("app", Language.RU))
        for seconds in (
            0,
            1,
            2,
            5,
            11,
            21,
            59,
            60,
            125,
            3601,
            86400,
            1234567,
            98765432,
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    messages.presence(seconds=seconds),
                    f"жизнь уже {format_duration_ru(seconds, depth=2)}.",
                )

    def test_english_units_and_conjunction(self) -> None:
        messages = AppMessages(catalog("app", Language.EN))
        self.assertEqual(messages.unit(unit=TimeUnit.YEAR, count=1), "1 year")
        self.assertEqual(messages.unit(unit=TimeUnit.YEAR, count=21), "21 years")
        self.assertEqual(
            messages.presence(seconds=125), "life for 2 minutes and 5 seconds."
        )
