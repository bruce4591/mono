from __future__ import annotations

import unittest

from market.backfill import ONLINE_BACKFILL_TABLES


class BackfillTests(unittest.TestCase):
    def test_backfill_tables_start_with_parent_tables(self):
        self.assertEqual(ONLINE_BACKFILL_TABLES[0], "instrument")
        self.assertLess(
            ONLINE_BACKFILL_TABLES.index("instrument"),
            ONLINE_BACKFILL_TABLES.index("bar_daily"),
        )
        self.assertLess(
            ONLINE_BACKFILL_TABLES.index("push_device"),
            ONLINE_BACKFILL_TABLES.index("mobile_alert_delivery"),
        )


if __name__ == "__main__":
    unittest.main()
