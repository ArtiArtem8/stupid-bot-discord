import unittest

from cogs.voice.profile.asset_cache import ProfileAssetCache


class TestProfileAssetCache(unittest.TestCase):
    def test_entry_limit_evicts_least_recently_used_image(self) -> None:
        cache = ProfileAssetCache(entries=2)
        cache.put("first", b"one")
        cache.put("second", b"two")
        self.assertEqual(cache.get("first"), b"one")
        cache.put("third", b"three")
        self.assertIsNone(cache.get("second"))
        self.assertEqual(cache.get("first"), b"one")
        self.assertEqual(cache.get("third"), b"three")

    def test_byte_limit_evicts_images_until_new_image_fits(self) -> None:
        cache = ProfileAssetCache(byte_limit=6)
        cache.put("first", b"aaa")
        cache.put("second", b"bbb")
        cache.put("third", b"cccc")
        self.assertIsNone(cache.get("first"))
        self.assertIsNone(cache.get("second"))
        self.assertEqual(cache.get("third"), b"cccc")

    def test_replacing_an_entry_counts_only_its_current_bytes(self) -> None:
        cache = ProfileAssetCache(byte_limit=6)
        cache.put("first", b"aaaa")
        cache.put("first", b"a")
        cache.put("second", b"bbbbb")
        self.assertEqual(cache.get("first"), b"a")
        self.assertEqual(cache.get("second"), b"bbbbb")

    def test_empty_or_oversized_input_does_not_evict_cached_images(self) -> None:
        cache = ProfileAssetCache(byte_limit=3)
        cache.put("first", b"aaa")
        cache.put("empty", b"")
        cache.put("oversized", b"xxxx")
        self.assertIsNone(cache.get("empty"))
        self.assertIsNone(cache.get("oversized"))
        self.assertEqual(cache.get("first"), b"aaa")

    def test_clear_releases_entries_and_resets_byte_budget(self) -> None:
        cache = ProfileAssetCache(byte_limit=3)
        cache.put("first", b"aaa")
        cache.clear()
        cache.put("second", b"bbb")
        self.assertIsNone(cache.get("first"))
        self.assertEqual(cache.get("second"), b"bbb")

    def test_limits_must_be_positive(self) -> None:
        for limits in ({"entries": 0}, {"byte_limit": 0}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                ProfileAssetCache(**limits)
