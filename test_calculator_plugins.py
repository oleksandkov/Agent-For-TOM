"""Standalone unit tests for calculator_plugins statistical functions.

Run:  python test_calculator_plugins.py
"""

import unittest

from calculator_plugins import mean, median, variance


class TestMean(unittest.TestCase):
    def test_mean_basic(self):
        self.assertEqual(mean([1, 2, 3, 4]), 2.5)

    def test_mean_single_value(self):
        self.assertEqual(mean([7]), 7.0)

    def test_mean_floats(self):
        self.assertAlmostEqual(mean([1.5, 2.5, 3.5]), 2.5)

    def test_mean_negative_numbers(self):
        self.assertEqual(mean([-1, -2, -3, -4]), -2.5)

    def test_mean_empty_raises(self):
        with self.assertRaises(ValueError):
            mean([])


class TestMedian(unittest.TestCase):
    def test_median_odd_count(self):
        self.assertEqual(median([3, 1, 2]), 2.0)

    def test_median_even_count(self):
        self.assertEqual(median([1, 2, 3, 4]), 2.5)

    def test_median_unsorted_input(self):
        self.assertEqual(median([10, 1, 7, 4, 3]), 4.0)

    def test_median_single_value(self):
        self.assertEqual(median([42]), 42.0)

    def test_median_duplicates(self):
        self.assertEqual(median([1, 1, 2, 2]), 1.5)

    def test_median_empty_raises(self):
        with self.assertRaises(ValueError):
            median([])


class TestVariance(unittest.TestCase):
    def test_variance_basic(self):
        self.assertAlmostEqual(variance([1, 2, 3, 4]), 5 / 3)

    def test_variance_two_values(self):
        self.assertAlmostEqual(variance([1, 3]), 2.0)

    def test_variance_identical_values(self):
        self.assertEqual(variance([5, 5, 5, 5]), 0.0)

    def test_variance_negative_numbers(self):
        self.assertAlmostEqual(variance([-2, -1, 0, 1, 2]), 2.5)

    def test_variance_single_value_raises(self):
        with self.assertRaises(ValueError):
            variance([1])

    def test_variance_empty_raises(self):
        with self.assertRaises(ValueError):
            variance([])


if __name__ == "__main__":
    unittest.main()
