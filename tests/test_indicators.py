from __future__ import annotations

import unittest

from market.indicators import evaluate_indicator_expression


class IndicatorTests(unittest.TestCase):
    def test_evaluate_indicator_expression_allows_basic_math(self):
        self.assertEqual(
            evaluate_indicator_expression(
                "last_price / max(volume_raw, 1)",
                {
                    "last_price": 100.0,
                    "volume_raw": 20.0,
                },
            ),
            5.0,
        )

    def test_evaluate_indicator_expression_rejects_imports(self):
        with self.assertRaises(ValueError):
            evaluate_indicator_expression("__import__('os').system('ls')", {})

    def test_evaluate_indicator_expression_rejects_unknown_names(self):
        with self.assertRaises(ValueError):
            evaluate_indicator_expression("open_interest * 2", {})


if __name__ == "__main__":
    unittest.main()
