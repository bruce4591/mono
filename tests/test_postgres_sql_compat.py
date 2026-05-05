from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = tuple((PROJECT_ROOT / "src" / "market").glob("*.py"))


class PostgresSqlCompatibilityTests(unittest.TestCase):
    def test_market_code_does_not_use_sqlite_only_insert_id(self):
        offenders = _matches(r"last_insert_rowid\s*\(", SOURCE_FILES)

        self.assertEqual(offenders, [])

    def test_market_code_does_not_compare_boolean_columns_to_integer_literals(self):
        offenders = _matches(
            r"\b(?:enabled|is_active|is_closed_bar|is_acknowledged)\s*=\s*[01]\b",
            SOURCE_FILES,
        )

        self.assertEqual(offenders, [])

    def test_market_code_passes_booleans_as_booleans(self):
        offenders = _matches(
            r"int\([^)]*\b(?:enabled|is_active|is_closed_bar|is_acknowledged)\b[^)]*\)",
            SOURCE_FILES,
        )

        self.assertEqual(offenders, [])


def _matches(pattern: str, paths: tuple[Path, ...]) -> list[str]:
    expression = re.compile(pattern)
    offenders = []
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if expression.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line_number}:{line.strip()}")
    return offenders


if __name__ == "__main__":
    unittest.main()
