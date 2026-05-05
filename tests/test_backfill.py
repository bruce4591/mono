from __future__ import annotations

import unittest

from market.backfill import ONLINE_BACKFILL_TABLES, insert_postgres_rows


class FakeCursor:
    def __init__(self):
        self.executions: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executions.append((sql, params))


class FakePostgresConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()

    def cursor(self):
        return self.cursor_instance


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

    def test_insert_postgres_rows_coerces_sqlite_booleans(self):
        connection = FakePostgresConnection()

        inserted = insert_postgres_rows(
            connection,
            table_name="instrument",
            columns=["instrument_id", "is_active"],
            rows=[{"instrument_id": 1, "is_active": 1}],
        )

        self.assertEqual(inserted, 1)
        params = connection.cursor_instance.executions[0][1]
        self.assertEqual(params[0], 1)
        self.assertIs(params[1], True)


if __name__ == "__main__":
    unittest.main()
