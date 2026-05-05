from __future__ import annotations

import unittest

from market.api_compare import compare_api_payloads, format_api_compare_report


class ApiCompareTests(unittest.TestCase):
    def test_compare_api_payloads_reports_matching_endpoints(self):
        payloads = {
            "http://primary/api/health": {"status": "ok"},
            "http://candidate/api/health": {"status": "ok"},
        }

        result = compare_api_payloads(
            "http://primary",
            "http://candidate",
            endpoints=["/api/health"],
            fetch_json=lambda url: payloads[url],
        )

        self.assertEqual(result.status, "match")
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.matched, ["/api/health"])
        self.assertEqual(result.differences, [])

    def test_compare_api_payloads_reports_first_json_difference_path(self):
        payloads = {
            "http://primary/api/health": {"latest_boards": [{"item_count": 30}]},
            "http://candidate/api/health": {"latest_boards": [{"item_count": 29}]},
        }

        result = compare_api_payloads(
            "http://primary/",
            "http://candidate/",
            endpoints=["/api/health"],
            fetch_json=lambda url: payloads[url],
        )

        self.assertEqual(result.status, "different")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.matched, [])
        self.assertEqual(len(result.differences), 1)
        self.assertEqual(result.differences[0].endpoint, "/api/health")
        self.assertEqual(result.differences[0].path, "latest_boards[0].item_count")
        self.assertEqual(result.differences[0].primary, 30)
        self.assertEqual(result.differences[0].candidate, 29)

    def test_compare_api_payloads_ignores_allowed_endpoint_path(self):
        payloads = {
            "http://primary/api/health": {"database": {"journal_mode": "wal"}},
            "http://candidate/api/health": {"database": {"journal_mode": "postgres"}},
        }

        result = compare_api_payloads(
            "http://primary",
            "http://candidate",
            endpoints=["/api/health"],
            ignore_paths={"/api/health": {"database.journal_mode"}},
            fetch_json=lambda url: payloads[url],
        )

        self.assertEqual(result.status, "match")
        self.assertEqual(result.matched, ["/api/health"])
        self.assertEqual(result.differences, [])

    def test_format_api_compare_report_summarizes_differences(self):
        payloads = {
            "http://primary/api/health": {"status": "ok"},
            "http://candidate/api/health": {"status": "stale"},
        }
        result = compare_api_payloads(
            "http://primary",
            "http://candidate",
            endpoints=["/api/health"],
            fetch_json=lambda url: payloads[url],
        )

        report = format_api_compare_report(result)

        self.assertIn("api payload diff: 0 matched, 1 different, 0 errors", report)
        self.assertIn("/api/health status", report)


if __name__ == "__main__":
    unittest.main()
