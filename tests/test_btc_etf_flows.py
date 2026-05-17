from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.btc_etf_flows import (
    BTC_ETF_FLOW_BOARD,
    BTC_ETF_FLOW_MARKET,
    parse_farside_btc_etf_flows,
    sync_farside_btc_etf_flows,
)
from market.db import connect, init_database
from market.models import DailyBar, Instrument
from market.repositories import DailyBarRepository, InstrumentRepository


SAMPLE_FARSIDE_HTML = """
<html>
  <body>
    <table>
      <tr>
        <th></th><th></th><th></th><th></th>
        <th></th><th></th><th></th><th>Total</th>
      </tr>
      <tr>
        <th></th><th>IBIT</th><th>FBTC</th><th>GBTC</th>
        <th>ARKB</th><th>BITB</th><th>BTCO</th><th></th>
      </tr>
      <tr>
        <td>16 May 2026</td><td>10.0</td><td>50.0</td><td>(70.0)</td>
        <td>40.0</td><td>30.0</td><td>20.0</td><td>80.0</td>
      </tr>
      <tr>
        <td>15 May 2026</td><td>-</td><td>25.0</td><td>(10.0)</td>
        <td>5.0</td><td>0.0</td><td>1.0</td><td>21.0</td>
      </tr>
    </table>
  </body>
</html>
"""


class BtcEtfFlowTests(unittest.TestCase):
    def test_parse_farside_btc_etf_flows_includes_fbtc_and_total(self):
        rows = parse_farside_btc_etf_flows(SAMPLE_FARSIDE_HTML)

        keyed = {(row.flow_date, row.fund_symbol): row.net_flow_usd_m for row in rows}
        self.assertEqual(keyed[("2026-05-16", "FBTC")], 50.0)
        self.assertEqual(keyed[("2026-05-16", "GBTC")], -70.0)
        self.assertEqual(keyed[("2026-05-16", "TOTAL")], 80.0)
        self.assertNotIn(("2026-05-15", "IBIT"), keyed)

    def test_sync_farside_btc_etf_flows_records_rows_bars_total_and_top_five(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "market.sqlite3"
            init_database(db_path)

            with connect(db_path) as connection:
                btc_id = InstrumentRepository(connection).upsert(
                    Instrument(
                        market="CRYPTO_FUTURES",
                        symbol="BTCUSDT",
                        display_name="BTCUSDT",
                        exchange="BINANCE",
                        instrument_type="crypto_futures",
                        quote_currency="USDT",
                        timezone="UTC",
                    )
                )
                DailyBarRepository(connection).upsert(
                    DailyBar(
                        instrument_id=btc_id,
                        trade_date="2026-05-16",
                        open=100000.0,
                        high=100000.0,
                        low=100000.0,
                        close=100000.0,
                        volume_raw=1.0,
                        turnover_raw=100000.0,
                        quote_currency="USDT",
                        source="test",
                    )
                )

                result = sync_farside_btc_etf_flows(
                    connection,
                    html=SAMPLE_FARSIDE_HTML,
                    limit_days=1,
                )
                fbtc_flow = connection.execute(
                    """
                    SELECT net_flow_usd_m, estimated_btc
                    FROM btc_etf_flow
                    WHERE flow_date = '2026-05-16'
                        AND fund_symbol = 'FBTC'
                    """
                ).fetchone()
                total_flow = connection.execute(
                    """
                    SELECT estimated_btc
                    FROM btc_etf_flow
                    WHERE flow_date = '2026-05-16'
                        AND fund_symbol = 'TOTAL'
                    """
                ).fetchone()
                fbtc_bar = connection.execute(
                    """
                    SELECT bar_daily.close
                    FROM bar_daily
                    JOIN instrument
                        ON instrument.instrument_id = bar_daily.instrument_id
                    WHERE instrument.market = ?
                        AND instrument.symbol = 'FBTC'
                        AND bar_daily.trade_date = '2026-05-16'
                    """,
                    (BTC_ETF_FLOW_MARKET,),
                ).fetchone()
                ranking_rows = connection.execute(
                    """
                    SELECT instrument.symbol
                    FROM ranking_snapshot
                    JOIN instrument
                        ON instrument.instrument_id = ranking_snapshot.instrument_id
                    WHERE ranking_snapshot.board_name = ?
                    ORDER BY ranking_snapshot.rank
                    """,
                    (BTC_ETF_FLOW_BOARD,),
                ).fetchall()

        self.assertEqual(result.rows_synced, 7)
        self.assertEqual(result.bars_synced, 7)
        self.assertEqual(result.rankings_synced, 5)
        self.assertEqual(float(fbtc_flow["net_flow_usd_m"]), 50.0)
        self.assertEqual(float(fbtc_flow["estimated_btc"]), 500.0)
        self.assertEqual(float(total_flow["estimated_btc"]), 800.0)
        self.assertEqual(float(fbtc_bar["close"]), 500.0)
        self.assertEqual(
            [str(row["symbol"]) for row in ranking_rows],
            ["GBTC", "FBTC", "ARKB", "BITB", "BTCO"],
        )


if __name__ == "__main__":
    unittest.main()
