"""Tests for blocking JSON validation and decoding."""

import unittest
from typing import override

from api.blocking_models import BlockedUser
from repositories.blocking_codec import try_decode_user


class TestBlockingCodec(unittest.TestCase):
    @override
    def setUp(self) -> None:
        self.valid = BlockedUser(
            user_id=42,
            current_username="user",
            current_global_name=None,
        ).to_dict()

    def test_valid_user_decodes_with_optional_global_name(self) -> None:
        decoded = try_decode_user(self.valid)

        self.assertIsNotNone(decoded)
        if decoded is None:
            self.fail("expected valid blocked user to decode")
        self.assertIsNone(decoded.current_global_name)

    def test_invalid_nested_history_is_rejected(self) -> None:
        invalid: object = {**self.valid, "block_history": [{"admin_id": "1"}]}

        self.assertIsNone(try_decode_user(invalid))

    def test_malformed_user_does_not_raise(self) -> None:
        self.assertIsNone(try_decode_user({"block_history": "invalid"}))
