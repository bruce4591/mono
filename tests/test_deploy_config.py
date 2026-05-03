from __future__ import annotations

import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DeployConfigTests(unittest.TestCase):
    def test_binance_ws_service_uses_top_usdt_limit_without_space_split_env(self):
        service = (
            REPO_ROOT / "deploy" / "systemd" / "market-binance-kline-ws.service"
        ).read_text(encoding="utf-8")

        self.assertIn("Environment=MARKET_WS_TOP_USDT_LIMIT=60", service)
        self.assertNotIn("Environment=MARKET_WS_SYMBOLS=BTCUSDT ETHUSDT", service)

    def test_binance_ws_script_fills_gaps_only_after_reconnect(self):
        script = (
            REPO_ROOT / "deploy" / "scripts" / "market-run-binance-kline-ws.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("--gap-fill-on-reconnect", script)

    def test_binance_futures_ws_service_uses_top_usdt_limit_and_gap_fill(self):
        service = (
            REPO_ROOT / "deploy" / "systemd" / "market-binance-futures-kline-ws.service"
        ).read_text(encoding="utf-8")
        script = (
            REPO_ROOT / "deploy" / "scripts" / "market-run-binance-futures-kline-ws.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("Environment=MARKET_FUTURES_WS_TOP_USDT_LIMIT=60", service)
        self.assertIn("run-binance-futures-kline-ws", script)
        self.assertIn("--gap-fill-on-reconnect", script)

    def test_deploy_scripts_used_by_systemd_are_executable(self):
        scripts = [
            REPO_ROOT / "deploy" / "scripts" / "market-run-binance-kline-ws.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-run-binance-futures-kline-ws.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-sync-crypto.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-sync-akshare-focus.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-sync-crypto-futures.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-aggregate-crypto.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-aggregate-crypto-futures.sh",
            REPO_ROOT / "deploy" / "scripts" / "market-fill-crypto-gaps.sh",
        ]

        for script in scripts:
            self.assertTrue(os.access(script, os.X_OK), f"{script} is not executable")

    def test_akshare_focus_sync_script_and_docs_are_present(self):
        script = (
            REPO_ROOT / "deploy" / "scripts" / "market-sync-akshare-focus.sh"
        ).read_text(encoding="utf-8")
        docs = (REPO_ROOT / "docs" / "deploy_tencent_lighthouse.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("sync-akshare-focus", script)
        self.assertIn("--board-limit 30", script)
        self.assertIn("--request-timeout-seconds 30", script)
        self.assertIn("--watchlist-config", script)
        self.assertIn("market-sync-akshare-focus.sh", docs)
        self.assertIn("A_SHARE_FOCUS20", docs)
        self.assertIn("HK_STOCK_FOCUS20", docs)
        self.assertIn("US_STOCK_FOCUS20", docs)
        self.assertIn("ETF_FOCUS20", docs)
        self.assertIn("INDEX_FOCUS20", docs)
        self.assertIn("COMMODITY_FOCUS20", docs)
        self.assertIn("30 13-21/2 * * 1-5", docs)
        self.assertIn("30 6 * * 2-6", docs)


if __name__ == "__main__":
    unittest.main()
