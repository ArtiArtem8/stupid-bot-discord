"""Retention and role counts use the selected catalog's plural rules."""

from unittest import TestCase

from stupidbot.features.member_roles.messages import RoleMessages
from stupidbot.i18n.catalog import catalog
from stupidbot.i18n.policy import Language


class RoleMessagesTests(TestCase):
    def test_russian_retention_and_role_counts(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.RU))
        for count, day, role in (
            (0, "дней", "ролей"),
            (1, "день", "роль"),
            (2, "дня", "роли"),
            (4, "дня", "роли"),
            (5, "дней", "ролей"),
            (11, "дней", "ролей"),
            (12, "дней", "ролей"),
            (14, "дней", "ролей"),
            (21, "день", "роль"),
            (22, "дня", "роли"),
            (25, "дней", "ролей"),
            (101, "день", "роль"),
            (111, "дней", "ролей"),
        ):
            with self.subTest(count=count):
                self.assertEqual(messages.days(days=count), f"{count} {day}")
                self.assertEqual(messages.ttl(days=count), f"TTL: {count} {day}")
                self.assertEqual(
                    messages.snapshot(name="Member", count=count, timestamp="<t:0:R>"),
                    f"- **Member** — {count} {role}, вышел <t:0:R>",
                )

    def test_russian_skipped_roles_agree_with_the_count(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.RU))
        for count, phrase in (
            (1, "Пропущена 1 роль (удалена, недоступна"),
            (2, "Пропущено 2 роли (удалены, недоступны"),
            (5, "Пропущено 5 ролей (удалены, недоступны"),
            (11, "Пропущено 11 ролей (удалены, недоступны"),
            (21, "Пропущена 21 роль (удалена, недоступна"),
            (22, "Пропущено 22 роли (удалены, недоступны"),
        ):
            with self.subTest(count=count):
                self.assertEqual(
                    messages.skipped(count=count),
                    f"\n\n{phrase} или запрос завершился ошибкой). "
                    "Существующие невыданные роли сохранены для повторной попытки.",
                )

    def test_english_retention_and_role_counts(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.EN))
        for count, day, role in (
            (0, "days", "roles"),
            (1, "day", "role"),
            (2, "days", "roles"),
            (11, "days", "roles"),
            (21, "days", "roles"),
        ):
            with self.subTest(count=count):
                self.assertEqual(messages.days(days=count), f"{count} {day}")
                self.assertEqual(messages.ttl(days=count), f"TTL: {count} {day}")
                self.assertEqual(
                    messages.snapshot(name="Member", count=count, timestamp="<t:0:R>"),
                    f"- **Member** — {count} {role}, left <t:0:R>",
                )
                self.assertEqual(
                    messages.skipped(count=count),
                    f"\n\nSkipped {count} {role} (deleted, inaccessible or the request "
                    "failed). Existing roles not yet granted have been kept for "
                    "another attempt.",
                )

    def test_korean_counts_keep_one_form(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.KO))
        for count in (0, 1, 2, 5, 11, 21, 100):
            with self.subTest(count=count):
                self.assertEqual(messages.days(days=count), f"{count}일")
                self.assertEqual(messages.ttl(days=count), f"보관 기간: {count}일")
                self.assertEqual(
                    messages.snapshot(name="Member", count=count, timestamp="<t:0:R>"),
                    f"- **Member**: 역할 {count}개, <t:0:R> 서버 나감",
                )
                self.assertEqual(
                    messages.skipped(count=count),
                    f"\n\n역할 {count}개를 건너뛰었습니다 "
                    "(삭제됨, 접근 불가 또는 요청 실패). "
                    "아직 부여하지 못한 기존 역할은 "
                    "다시 시도할 수 있도록 보관했습니다.",
                )

    def test_korean_enabled_message_labels_retention_once(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.KO))
        for retention, expected in (
            (messages.ttl(days=1), "보관 기간: 1일"),
            (messages.catalog.pgettext("ui", "unlimited retention"), "무기한"),
        ):
            with self.subTest(retention=retention):
                self.assertEqual(
                    messages.enabled(retention=retention),
                    f"멤버가 다시 들어오면 봇이 역할을 복원합니다 ({expected}).",
                )

    def test_russian_preview_total_avoids_inflecting_the_snapshot_count(self) -> None:
        messages = RoleMessages(catalog("member_roles", Language.RU))
        for count in (11, 21, 22, 25):
            with self.subTest(count=count):
                self.assertEqual(
                    messages.footer(total=count),
                    f"Показаны первые 10 снимков из {count}",
                )
