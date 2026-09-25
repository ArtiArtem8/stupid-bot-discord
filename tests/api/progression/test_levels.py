import unittest
from fractions import Fraction

from api.progression.levels import LevelPolicy


class TestLevels(unittest.TestCase):
    def test_known_thresholds(self) -> None:
        policy = LevelPolicy()
        for level, xp in (
            (1, 0),
            (2, 250),
            (5, 4000),
            (10, 20250),
            (20, 90250),
            (35, 289000),
            (50, 600250),
            (75, 1369000),
            (100, 2450250),
            (150, 5550250),
        ):
            with self.subTest(level=level):
                self.assertEqual(policy.threshold(level), xp)
                if level > 1:
                    self.assertEqual(policy.level_for(xp - Fraction(1, 10)), level - 1)
                self.assertEqual(policy.level_for(xp), level)
                self.assertEqual(policy.level_for(xp + Fraction(1, 10)), level)

    def test_every_boundary_just_before_at_and_after(self) -> None:
        policy = LevelPolicy()
        epsilon = Fraction(1, 10**20)
        for level in range(2, 301):
            xp = policy.threshold(level)
            with self.subTest(level=level):
                self.assertEqual(policy.level_for(xp - epsilon), level - 1)
                self.assertEqual(policy.level_for(xp), level)
                self.assertEqual(policy.level_for(xp + epsilon), level)

    def test_progress_example(self) -> None:
        progress = LevelPolicy().progress(5075)
        self.assertEqual(progress.level, 5)
        self.assertEqual(
            (progress.current_threshold, progress.next_threshold), (4000, 6250)
        )
        self.assertEqual(
            (progress.earned, progress.required, progress.remaining),
            (1075, 2250, 1175),
        )
        self.assertEqual(progress.ratio, Fraction(43, 90))

    def test_fractional_progress_is_not_truncated(self) -> None:
        result = LevelPolicy().progress(Fraction(20375, 4))
        self.assertEqual(result.earned, Fraction(4375, 4))
        self.assertEqual(result.remaining, Fraction(4625, 4))

    def test_zero_starts_at_level_one(self) -> None:
        result = LevelPolicy().progress(0)
        self.assertEqual((result.level, result.earned, result.required), (1, 0, 250))

    def test_one_million_xp_is_level_sixty_four(self) -> None:
        progress = LevelPolicy().progress(1_000_000)
        self.assertEqual(progress.level, 64)
        self.assertEqual(progress.current_threshold, 992250)
        self.assertEqual(progress.next_threshold, 1024000)

    def test_huge_threshold_has_no_floating_point_off_by_one(self) -> None:
        policy = LevelPolicy()
        level = 10**50
        threshold = policy.threshold(level)
        self.assertEqual(policy.level_for(threshold - Fraction(1, 10**30)), level - 1)
        self.assertEqual(policy.level_for(threshold), level)

    def test_scale_changes_thresholds_without_changing_formula(self) -> None:
        policy = LevelPolicy(500)
        self.assertEqual(policy.threshold(5), 8000)
        self.assertEqual(policy.level_for(8000), 5)

    def test_invalid_inputs_fail_explicitly(self) -> None:
        with self.assertRaises(ValueError):
            LevelPolicy(0)
        with self.assertRaises(ValueError):
            LevelPolicy().threshold(0)
        with self.assertRaises(ValueError):
            LevelPolicy().progress(Fraction(-1))
        with self.assertRaises(TypeError):
            LevelPolicy().threshold(True)
        with self.assertRaises(TypeError):
            LevelPolicy().progress(True)
