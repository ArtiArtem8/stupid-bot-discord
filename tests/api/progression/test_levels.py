import unittest
from fractions import Fraction

from api.progression.levels import LevelPolicy


class TestLevels(unittest.TestCase):
    def test_known_thresholds(self) -> None:
        policy = LevelPolicy()
        for level, xp in (
            (1, 0),
            (2, 2250),
            (3, 4900),
            (4, 7950),
            (5, 11400),
            (10, 34650),
            (20, 111150),
            (35, 300900),
            (50, 580650),
            (75, 1246900),
            (100, 2163150),
            (150, 4745650),
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
        self.assertEqual(progress.level, 3)
        self.assertEqual(
            (progress.current_threshold, progress.next_threshold), (4900, 7950)
        )
        self.assertEqual(
            (progress.earned, progress.required, progress.remaining),
            (175, 3050, 2875),
        )
        self.assertEqual(progress.ratio, Fraction(7, 122))

    def test_fractional_progress_is_not_truncated(self) -> None:
        result = LevelPolicy().progress(Fraction(20375, 4))
        self.assertEqual(result.earned, Fraction(775, 4))
        self.assertEqual(result.remaining, Fraction(11425, 4))

    def test_zero_starts_at_level_one(self) -> None:
        result = LevelPolicy().progress(0)
        self.assertEqual((result.level, result.earned, result.required), (1, 0, 2250))

    def test_one_million_xp_is_level_sixty_six(self) -> None:
        progress = LevelPolicy().progress(1_000_000)
        self.assertEqual(progress.level, 66)
        self.assertEqual(progress.current_threshold, 978250)
        self.assertEqual(progress.next_threshold, 1006500)

    def test_huge_threshold_has_no_floating_point_off_by_one(self) -> None:
        policy = LevelPolicy()
        level = 10**50
        threshold = policy.threshold(level)
        self.assertEqual(policy.level_for(threshold - Fraction(1, 10**30)), level - 1)
        self.assertEqual(policy.level_for(threshold), level)
        self.assertEqual(policy.level_for(threshold + Fraction(1, 10**30)), level)

    def test_scale_changes_thresholds_without_changing_formula(self) -> None:
        default = LevelPolicy()
        policy = LevelPolicy(quadratic_coefficient=400, linear_coefficient=4100)
        for level in (1, 2, 5, 10, 100, 300):
            with self.subTest(level=level):
                xp = 2 * default.threshold(level)
                self.assertEqual(policy.threshold(level), xp)
                self.assertEqual(policy.level_for(xp), level)
                if level > 1:
                    self.assertEqual(policy.level_for(xp - Fraction(1, 10)), level - 1)

    def test_zero_linear_coefficient_allows_pure_quadratic_curve(self) -> None:
        policy = LevelPolicy(quadratic_coefficient=250, linear_coefficient=0)
        for level in (1, 2, 5, 10, 300, 10**50):
            with self.subTest(level=level):
                xp = 250 * (level - 1) ** 2
                self.assertEqual(policy.threshold(level), xp)
                self.assertEqual(policy.level_for(xp), level)
                if level > 1:
                    self.assertEqual(policy.level_for(xp - Fraction(1, 10)), level - 1)

    def test_invalid_inputs_fail_explicitly(self) -> None:
        for coefficient in (0, -1):
            with self.subTest(quadratic=coefficient), self.assertRaises(ValueError):
                LevelPolicy(quadratic_coefficient=coefficient)
        with self.assertRaises(ValueError):
            LevelPolicy(linear_coefficient=-1)
        with self.assertRaises(TypeError):
            LevelPolicy(quadratic_coefficient=True)
        with self.assertRaises(TypeError):
            LevelPolicy(linear_coefficient=True)
        with self.assertRaises(ValueError):
            LevelPolicy().threshold(0)
        with self.assertRaises(ValueError):
            LevelPolicy().progress(Fraction(-1))
        with self.assertRaises(TypeError):
            LevelPolicy().threshold(True)
        with self.assertRaises(TypeError):
            LevelPolicy().progress(True)
