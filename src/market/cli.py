from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from market.alerts import evaluate_alert_rules
from market.aggregators import aggregate_crypto_from_1m, aggregate_market_from_1m
from market.api import serve_api
from market.binance import (
    fetch_binance_24hr_tickers,
    fetch_top_binance_usdt_symbols,
    select_top_quote_volume_symbols,
    sync_binance_24hr_snapshots,
    sync_binance_daily_bars,
    sync_binance_klines,
)
from market.binance_futures import (
    binance_futures_symbol_to_instrument,
    fetch_binance_futures_24hr_tickers,
    fetch_binance_futures_exchange_info,
    parse_binance_futures_24hr_ticker_snapshot,
    select_futures_tradefi_symbols,
    select_top_futures_usdt_symbols,
)
from market.collectors.akshare import AkshareCollector
from market.collectors.binance import BinanceCollector
from market.collectors.binance_ws import BinanceKlineWebSocketCollector
from market.collectors.base import CollectorResult, run_collector_job
from market.crypto_gaps import fill_binance_1m_gaps
from market.crypto_gaps import fill_binance_futures_1m_gaps
from market.db import connect, init_database
from market.models import AlertRule, WatchlistEntry
from market.realtime import apply_binance_futures_kline_event, apply_binance_ticker_event
from market.repositories import (
    AlertEventRepository,
    AlertRuleRepository,
    InstrumentRepository,
    MarketSnapshotRepository,
    RankingRepository,
    WatchlistRepository,
)
from market.sample_data import seed_sample_data
from market.settings import load_settings
from market.watchlists import sync_watchlist_from_file


REPO_ROOT = Path(__file__).resolve().parents[2]
AKSHARE_FOCUS_WATCHLISTS = ["ETF_FOCUS20", "INDEX_FOCUS20"]
AKSHARE_FOCUS_CONFIGS = [
    REPO_ROOT / "config" / "watchlists" / "etf_focus20.json",
    REPO_ROOT / "config" / "watchlists" / "index_focus20.json",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market")
    parser.add_argument("--version", action="version", version="market 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Initialize the SQLite database")
    init_db.add_argument("--db-path", type=Path, default=None)

    health_check = subparsers.add_parser(
        "health-check", help="Check whether the SQLite database is readable"
    )
    health_check.add_argument("--db-path", type=Path, default=None)

    sync_watchlists = subparsers.add_parser(
        "sync-watchlists", help="Import a static watchlist JSON file"
    )
    sync_watchlists.add_argument("--db-path", type=Path, default=None)
    sync_watchlists.add_argument("--path", type=Path, required=True)

    refresh_rankings = subparsers.add_parser(
        "refresh-rankings", help="Refresh a turnover ranking board"
    )
    refresh_rankings.add_argument("--db-path", type=Path, default=None)
    refresh_rankings.add_argument("--board-name", required=True)
    refresh_rankings.add_argument("--snapshot-ts-utc", required=True)
    refresh_rankings.add_argument("--trade-date-local", required=True)
    refresh_rankings.add_argument("--market", required=True)
    refresh_rankings.add_argument("--instrument-type", required=True)
    refresh_rankings.add_argument("--limit", type=int, required=True)
    refresh_rankings.add_argument("--watchlist-name", default=None)

    seed_sample_data_parser = subparsers.add_parser(
        "seed-sample-data", help="Write deterministic fake market data for local smoke tests"
    )
    seed_sample_data_parser.add_argument("--db-path", type=Path, default=None)
    seed_sample_data_parser.add_argument("--snapshot-ts-utc", required=True)
    seed_sample_data_parser.add_argument("--trade-date-local", required=True)

    serve_api_parser = subparsers.add_parser(
        "serve-api", help="Serve the read-only local JSON API"
    )
    serve_api_parser.add_argument("--db-path", type=Path, default=None)
    serve_api_parser.add_argument("--host", default="127.0.0.1")
    serve_api_parser.add_argument("--port", type=int, default=8000)
    serve_api_parser.add_argument("--dry-run", action="store_true")

    sync_binance = subparsers.add_parser(
        "sync-binance-klines", help="Fetch Binance Spot klines and upsert intraday bars"
    )
    sync_binance.add_argument("--db-path", type=Path, default=None)
    sync_binance.add_argument("--symbol", required=True)
    sync_binance.add_argument("--interval", default="15m")
    sync_binance.add_argument("--limit", type=int, default=96)
    sync_binance.add_argument("--dry-run", action="store_true")

    sync_binance_daily = subparsers.add_parser(
        "sync-binance-daily", help="Fetch Binance Spot 1d klines and upsert daily bars"
    )
    sync_binance_daily.add_argument("--db-path", type=Path, default=None)
    sync_binance_daily.add_argument("--symbol", required=True)
    sync_binance_daily.add_argument("--days", type=int, default=365)
    sync_binance_daily.add_argument("--dry-run", action="store_true")

    sync_crypto_board = subparsers.add_parser(
        "sync-crypto-board",
        help="Sync multiple Binance symbols and refresh the crypto turnover board",
    )
    sync_crypto_board.add_argument("--db-path", type=Path, default=None)
    sync_crypto_board.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Crypto symbol to sync; can be provided multiple times",
    )
    sync_crypto_board.add_argument("--interval", default="1m")
    sync_crypto_board.add_argument("--limit", type=int, default=96)
    sync_crypto_board.add_argument("--board-name", default="CRYPTO_TURNOVER_TOP50")
    sync_crypto_board.add_argument("--board-limit", type=int, default=50)
    sync_crypto_board.add_argument("--top-usdt-limit", type=int, default=60)
    sync_crypto_board.add_argument("--snapshot-ts-utc", default=None)
    sync_crypto_board.add_argument("--trade-date-local", default=None)
    sync_crypto_board.add_argument("--skip-kline-sync", action="store_true")
    sync_crypto_board.add_argument("--dry-run", action="store_true")

    sync_crypto_futures = subparsers.add_parser(
        "sync-crypto-futures-boards",
        help="Sync Binance USD-M futures turnover boards",
    )
    sync_crypto_futures.add_argument("--db-path", type=Path, default=None)
    sync_crypto_futures.add_argument("--board-limit", type=int, default=50)
    sync_crypto_futures.add_argument("--snapshot-ts-utc", default=None)
    sync_crypto_futures.add_argument("--trade-date-local", default=None)
    sync_crypto_futures.add_argument("--dry-run", action="store_true")

    aggregate_crypto = subparsers.add_parser(
        "aggregate-crypto-klines",
        help="Aggregate local crypto 1m bars into higher intervals without REST sync",
    )
    aggregate_crypto.add_argument("--db-path", type=Path, default=None)
    aggregate_crypto.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Crypto symbol to aggregate; can be provided multiple times",
    )
    aggregate_crypto.add_argument("--top-usdt-limit", type=int, default=60)
    aggregate_crypto.add_argument("--started-at-utc", default=None)
    aggregate_crypto.add_argument("--dry-run", action="store_true")

    aggregate_futures = subparsers.add_parser(
        "aggregate-crypto-futures-klines",
        help="Aggregate local Binance USD-M futures 1m bars into higher intervals",
    )
    aggregate_futures.add_argument("--db-path", type=Path, default=None)
    aggregate_futures.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Futures symbol to aggregate; can be provided multiple times",
    )
    aggregate_futures.add_argument("--top-usdt-limit", type=int, default=60)
    aggregate_futures.add_argument("--dry-run", action="store_true")

    fill_crypto_gaps = subparsers.add_parser(
        "fill-crypto-kline-gaps",
        help="Fill missing Binance 1m crypto bars with REST and aggregate local periods",
    )
    fill_crypto_gaps.add_argument("--db-path", type=Path, default=None)
    fill_crypto_gaps.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Crypto symbol to check; can be provided multiple times",
    )
    fill_crypto_gaps.add_argument("--lookback-minutes", type=int, default=180)
    fill_crypto_gaps.add_argument("--top-usdt-limit", type=int, default=60)
    fill_crypto_gaps.add_argument("--end-ts-utc", default=None)
    fill_crypto_gaps.add_argument("--dry-run", action="store_true")

    sync_crypto_daily = subparsers.add_parser(
        "sync-crypto-daily",
        help="Sync Binance Spot 1d bars for top USDT symbols or explicit symbols",
    )
    sync_crypto_daily.add_argument("--db-path", type=Path, default=None)
    sync_crypto_daily.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Crypto symbol to sync; can be provided multiple times",
    )
    sync_crypto_daily.add_argument("--days", type=int, default=365)
    sync_crypto_daily.add_argument("--top-usdt-limit", type=int, default=50)
    sync_crypto_daily.add_argument("--snapshot-ts-utc", default=None)
    sync_crypto_daily.add_argument("--dry-run", action="store_true")

    sync_akshare_focus = subparsers.add_parser(
        "sync-akshare-focus",
        help="Sync AKShare ETF and index focus watchlists and refresh their boards",
    )
    sync_akshare_focus.add_argument("--db-path", type=Path, default=None)
    sync_akshare_focus.add_argument("--days", type=int, default=365)
    sync_akshare_focus.add_argument("--snapshot-ts-utc", default=None)
    sync_akshare_focus.add_argument("--trade-date-local", default=None)
    sync_akshare_focus.add_argument("--board-limit", type=int, default=20)
    sync_akshare_focus.add_argument(
        "--watchlist-config",
        action="append",
        default=[],
        type=Path,
        help="Watchlist config to import before syncing; can be provided multiple times",
    )
    sync_akshare_focus.add_argument("--dry-run", action="store_true")

    apply_ticker = subparsers.add_parser(
        "apply-binance-ticker-event",
        help="Apply one Binance ticker WebSocket event JSON payload to market_snapshot",
    )
    apply_ticker.add_argument("--db-path", type=Path, default=None)
    apply_ticker.add_argument("--path", type=Path, required=True)

    run_kline_ws = subparsers.add_parser(
        "run-binance-kline-ws",
        help="Run Binance combined WebSocket 1m kline collector",
    )
    run_kline_ws.add_argument("--db-path", type=Path, default=None)
    run_kline_ws.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Crypto symbol to subscribe; can be provided multiple times",
    )
    run_kline_ws.add_argument("--interval", default="1m")
    run_kline_ws.add_argument("--max-streams-per-connection", type=int, default=200)
    run_kline_ws.add_argument("--top-usdt-limit", type=int, default=0)
    run_kline_ws.add_argument("--gap-fill-on-reconnect", action="store_true")
    run_kline_ws.add_argument("--dry-run", action="store_true")

    run_futures_kline_ws = subparsers.add_parser(
        "run-binance-futures-kline-ws",
        help="Run Binance USD-M futures combined WebSocket 1m kline collector",
    )
    run_futures_kline_ws.add_argument("--db-path", type=Path, default=None)
    run_futures_kline_ws.add_argument(
        "--symbol",
        action="append",
        default=[],
        help="Futures symbol to subscribe; can be provided multiple times",
    )
    run_futures_kline_ws.add_argument("--interval", default="1m")
    run_futures_kline_ws.add_argument("--max-streams-per-connection", type=int, default=200)
    run_futures_kline_ws.add_argument("--top-usdt-limit", type=int, default=60)
    run_futures_kline_ws.add_argument("--gap-fill-on-reconnect", action="store_true")
    run_futures_kline_ws.add_argument("--dry-run", action="store_true")

    add_alert_rule = subparsers.add_parser(
        "add-alert-rule", help="Create or update a threshold alert rule"
    )
    add_alert_rule.add_argument("--db-path", type=Path, default=None)
    add_alert_rule.add_argument("--name", required=True)
    add_alert_rule.add_argument("--market", required=True)
    add_alert_rule.add_argument("--symbol", required=True)
    add_alert_rule.add_argument("--metric", required=True)
    add_alert_rule.add_argument("--operator", required=True)
    add_alert_rule.add_argument("--threshold", type=float, required=True)

    run_alerts = subparsers.add_parser(
        "run-alerts", help="Evaluate active alert rules against latest snapshots"
    )
    run_alerts.add_argument("--db-path", type=Path, default=None)
    run_alerts.add_argument("--triggered-at-utc", default=None)

    list_alert_events = subparsers.add_parser(
        "list-alert-events", help="Print recent alert events as JSON"
    )
    list_alert_events.add_argument("--db-path", type=Path, default=None)
    list_alert_events.add_argument("--limit", type=int, default=50)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    db_path = args.db_path or settings.db_path

    if args.command == "init-db":
        init_database(db_path)
        print(f"database initialized: {db_path}")
        return 0

    if args.command == "health-check":
        try:
            with connect(db_path) as connection:
                connection.execute("SELECT 1 FROM instrument LIMIT 1").fetchone()
        except sqlite3.Error as error:
            print(f"database: error: {error}")
            return 1
        print("database: ok")
        return 0

    if args.command == "sync-watchlists":
        with connect(db_path) as connection:
            count = sync_watchlist_from_file(connection, args.path)
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        print(f"watchlist synced: {payload['watchlist_name']} ({count} entries)")
        return 0

    if args.command == "refresh-rankings":
        with connect(db_path) as connection:
            count = RankingRepository(connection).refresh_turnover_board(
                board_name=args.board_name,
                snapshot_ts_utc=args.snapshot_ts_utc,
                trade_date_local=args.trade_date_local,
                market=args.market,
                instrument_type=args.instrument_type,
                limit=args.limit,
                watchlist_name=args.watchlist_name,
            )
        print(f"ranking refreshed: {args.board_name} ({count} rows)")
        return 0

    if args.command == "seed-sample-data":
        with connect(db_path) as connection:
            result = seed_sample_data(
                connection,
                snapshot_ts_utc=args.snapshot_ts_utc,
                trade_date_local=args.trade_date_local,
            )
        print(
            "sample data seeded: "
            f"{result.instruments} instruments, "
            f"{result.snapshots} snapshots, "
            f"{result.daily_bars} daily bars, "
            f"{result.intraday_bars} intraday bars"
        )
        return 0

    if args.command == "serve-api":
        if args.dry_run:
            print(f"api ready: http://{args.host}:{args.port}")
            return 0
        serve_api(db_path=db_path, host=args.host, port=args.port)
        return 0

    if args.command == "sync-binance-klines":
        if args.dry_run:
            print(
                "binance klines ready: "
                f"{args.symbol.upper()} {args.interval} limit={args.limit}"
            )
            return 0
        with connect(db_path) as connection:
            result = sync_binance_klines(
                connection,
                symbol=args.symbol,
                interval=args.interval,
                limit=args.limit,
            )
        print(
            "binance klines synced: "
            f"{result.symbol} {result.interval} "
            f"({result.bars} bars, latest_close={result.latest_close})"
        )
        return 0

    if args.command == "sync-binance-daily":
        if args.dry_run:
            print(f"binance daily ready: {args.symbol.upper()} 1d days={args.days}")
            return 0
        with connect(db_path) as connection:
            result = sync_binance_daily_bars(
                connection,
                symbol=args.symbol,
                days=args.days,
            )
        print(
            "binance daily synced: "
            f"{result.symbol} {result.interval} "
            f"({result.bars} bars, latest_close={result.latest_close})"
        )
        return 0

    if args.command == "aggregate-crypto-klines":
        now = datetime.now(tz=UTC)
        started_at_utc = args.started_at_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        if args.dry_run:
            normalized_symbols = [symbol.upper() for symbol in args.symbol]
            symbol_text = ",".join(normalized_symbols) or f"top_usdt_limit={args.top_usdt_limit}"
            print(f"crypto kline aggregate ready: {symbol_text}")
            return 0
        with connect(db_path) as connection:
            normalized_symbols = _crypto_symbols_for_aggregation(
                connection,
                symbols=args.symbol,
                limit=args.top_usdt_limit,
            )
            checkpoint = ",".join(normalized_symbols) + ":local_1m"

            def aggregate_local():
                result = aggregate_crypto_from_1m(connection, normalized_symbols)
                return CollectorResult(
                    source_name="local_aggregate",
                    items_synced=result.bars_written,
                    metadata={"symbols": normalized_symbols, "source_interval": "1m"},
                )

            result = run_collector_job(
                connection,
                job_name="aggregate-crypto-klines",
                source_name="local_aggregate",
                checkpoint=checkpoint,
                started_at_utc=started_at_utc,
                operation=aggregate_local,
            )
        print(
            "crypto klines aggregated: "
            f"{len(normalized_symbols)} symbols, {result.items_synced} bars"
        )
        return 0

    if args.command == "sync-crypto-futures-boards":
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        trade_date_local = args.trade_date_local or now.date().isoformat()
        if args.dry_run:
            print(
                "crypto futures board sync ready: "
                f"limit={args.board_limit} snapshot={snapshot_ts_utc}"
            )
            return 0
        tickers = fetch_binance_futures_24hr_tickers()
        exchange_info = fetch_binance_futures_exchange_info()
        with connect(db_path) as connection:
            result = run_collector_job(
                connection,
                job_name="sync-crypto-futures-boards",
                source_name="binance_futures",
                checkpoint=f"usd_m:{snapshot_ts_utc}:{args.board_limit}",
                started_at_utc=snapshot_ts_utc,
                operation=lambda: _sync_crypto_futures_boards(
                    connection,
                    tickers=tickers,
                    exchange_info=exchange_info,
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=trade_date_local,
                    board_limit=args.board_limit,
                ),
            )
        print(
            "crypto futures boards synced: "
            f"{result.items_synced} tickers, snapshot={snapshot_ts_utc}"
        )
        return 0

    if args.command == "sync-crypto-board":
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        trade_date_local = args.trade_date_local or now.date().isoformat()
        if args.dry_run:
            normalized_symbols = [symbol.upper() for symbol in args.symbol]
            symbol_text = ",".join(normalized_symbols) or f"top_usdt_limit={args.top_usdt_limit}"
            print(
                "crypto board sync ready: "
                f"{symbol_text} "
                f"{args.interval} limit={args.limit} board={args.board_name}"
            )
            return 0
        tickers = fetch_binance_24hr_tickers()
        symbols = args.symbol or select_top_quote_volume_symbols(
            tickers,
            quote_asset="USDT",
            limit=args.top_usdt_limit,
        )
        normalized_symbols = [symbol.upper() for symbol in symbols]
        with connect(db_path) as connection:
            checkpoint = ",".join(normalized_symbols) + f":{args.interval}"
            ranking_count = 0

            def sync_and_rank():
                nonlocal ranking_count
                if args.skip_kline_sync:
                    result = CollectorResult(
                        source_name="binance",
                        items_synced=0,
                        metadata={
                            "symbols": normalized_symbols,
                            "interval": args.interval,
                            "skip_kline_sync": True,
                        },
                    )
                else:
                    result = BinanceCollector().sync_intraday_bars(
                        connection,
                        normalized_symbols,
                        interval=args.interval,
                        limit=args.limit,
                    )
                sync_binance_24hr_snapshots(
                    connection,
                    tickers=tickers,
                    symbols=normalized_symbols,
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=trade_date_local,
                )
                if args.interval == "1m" and not args.skip_kline_sync:
                    aggregate_crypto_from_1m(connection, normalized_symbols)
                ranking_count = RankingRepository(connection).refresh_turnover_board(
                    board_name=args.board_name,
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=trade_date_local,
                    market="CRYPTO",
                    instrument_type="crypto",
                    limit=args.board_limit,
                )
                return result

            run_collector_job(
                connection,
                job_name="sync-crypto-board",
                source_name="binance",
                checkpoint=checkpoint,
                started_at_utc=snapshot_ts_utc,
                operation=sync_and_rank,
            )
        print(
            "crypto board synced: "
            f"{len(normalized_symbols)} symbols, {ranking_count} ranking rows, "
            f"snapshot={snapshot_ts_utc}"
        )
        return 0

    if args.command == "aggregate-crypto-futures-klines":
        symbols = args.symbol or _futures_ws_symbols(args.top_usdt_limit)
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if args.dry_run:
            symbol_text = ",".join(normalized_symbols) or "none"
            print(f"crypto futures aggregate ready: {symbol_text}")
            return 0
        with connect(db_path) as connection:
            result = aggregate_market_from_1m(
                connection,
                market="CRYPTO_FUTURES",
                symbols=normalized_symbols,
            )
        print(
            "crypto futures aggregate complete: "
            f"{len(normalized_symbols)} symbols, {result.bars_written} bars"
        )
        return 0

    if args.command == "fill-crypto-kline-gaps":
        end = _parse_utc_arg(args.end_ts_utc) if args.end_ts_utc else datetime.now(tz=UTC)
        start = end - timedelta(minutes=args.lookback_minutes)
        start_ts_utc = _format_utc_arg(start)
        end_ts_utc = _format_utc_arg(end)
        symbols = args.symbol or fetch_top_binance_usdt_symbols(limit=args.top_usdt_limit)
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if args.dry_run:
            symbol_text = ",".join(normalized_symbols) or "none"
            print(
                "crypto gap fill ready: "
                f"{symbol_text} 1m lookback={args.lookback_minutes}m"
            )
            return 0
        with connect(db_path) as connection:
            checkpoint = ",".join(normalized_symbols) + f":1m:{start_ts_utc}:{end_ts_utc}"
            result = run_collector_job(
                connection,
                job_name="fill-crypto-kline-gaps",
                source_name="binance",
                checkpoint=checkpoint,
                started_at_utc=end_ts_utc,
                operation=lambda: fill_binance_1m_gaps(
                    connection,
                    symbols=normalized_symbols,
                    start_ts_utc=start_ts_utc,
                    end_ts_utc=end_ts_utc,
                ),
            )
        print(
            "crypto gaps filled: "
            f"{result.symbols_checked} symbols, {result.gaps_filled} missing minutes, "
            f"{result.bars_written} bars, {result.aggregate_bars_written} aggregate bars"
        )
        return 0

    if args.command == "sync-crypto-daily":
        symbols = args.symbol or fetch_top_binance_usdt_symbols(limit=args.top_usdt_limit)
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if args.dry_run:
            print(
                "crypto daily sync ready: "
                f"{','.join(normalized_symbols)} days={args.days}"
            )
            return 0
        with connect(db_path) as connection:
            checkpoint = ",".join(normalized_symbols) + f":1d:{args.days}"

            result = run_collector_job(
                connection,
                job_name="sync-crypto-daily",
                source_name="binance",
                checkpoint=checkpoint,
                started_at_utc=snapshot_ts_utc,
                operation=lambda: BinanceCollector().sync_daily_bars(
                    connection,
                    normalized_symbols,
                    days=args.days,
                ),
            )
        print(
            "crypto daily synced: "
            f"{len(normalized_symbols)} symbols, {result.items_synced} daily bars, "
            f"snapshot={snapshot_ts_utc}"
        )
        return 0

    if args.command == "sync-akshare-focus":
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        watchlist_configs = args.watchlist_config or AKSHARE_FOCUS_CONFIGS
        if args.dry_run:
            print(
                "akshare focus sync ready: "
                f"{','.join(AKSHARE_FOCUS_WATCHLISTS)} days={args.days}"
            )
            return 0
        with connect(db_path) as connection:
            for config_path in watchlist_configs:
                sync_watchlist_from_file(connection, config_path)
            ranking_counts: dict[str, int] = {}

            def sync_and_rank():
                result = AkshareCollector().sync_focus(
                    connection,
                    watchlist_names=AKSHARE_FOCUS_WATCHLISTS,
                    days=args.days,
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=args.trade_date_local,
                )
                ranking_trade_date = str(
                    result.metadata.get("trade_date_local")
                    or args.trade_date_local
                    or now.date().isoformat()
                )
                ranking = RankingRepository(connection)
                ranking_counts["ETF_FOCUS20"] = ranking.refresh_turnover_board(
                    board_name="ETF_FOCUS20",
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=ranking_trade_date,
                    market="US",
                    instrument_type="etf",
                    limit=args.board_limit,
                    watchlist_name="ETF_FOCUS20",
                )
                ranking_counts["INDEX_FOCUS20"] = ranking.refresh_turnover_board(
                    board_name="INDEX_FOCUS20",
                    snapshot_ts_utc=snapshot_ts_utc,
                    trade_date_local=ranking_trade_date,
                    market="US",
                    instrument_type="index",
                    limit=args.board_limit,
                    watchlist_name="INDEX_FOCUS20",
                )
                return result

            result = run_collector_job(
                connection,
                job_name="sync-akshare-focus",
                source_name="akshare",
                checkpoint=f"{','.join(AKSHARE_FOCUS_WATCHLISTS)}:1d:{args.days}",
                started_at_utc=snapshot_ts_utc,
                operation=sync_and_rank,
            )
        print(
            "akshare focus synced: "
            f"{result.items_synced} daily bars, "
            f"ETF_FOCUS20={ranking_counts.get('ETF_FOCUS20', 0)}, "
            f"INDEX_FOCUS20={ranking_counts.get('INDEX_FOCUS20', 0)}, "
            f"snapshot={snapshot_ts_utc}"
        )
        return 0

    if args.command == "apply-binance-ticker-event":
        payload = json.loads(args.path.read_text(encoding="utf-8"))
        with connect(db_path) as connection:
            event = apply_binance_ticker_event(connection, payload)
        print(
            "binance ticker applied: "
            f"{event.symbol} last_price={event.last_price} "
            f"snapshot={event.snapshot_ts_utc}"
        )
        return 0

    if args.command == "run-binance-kline-ws":
        symbols = args.symbol or (
            fetch_top_binance_usdt_symbols(limit=args.top_usdt_limit)
            if args.top_usdt_limit > 0
            else []
        )
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if args.dry_run:
            symbol_text = ",".join(normalized_symbols) or "none"
            print(
                "binance kline ws ready: "
                f"{symbol_text} interval={args.interval}"
            )
            return 0
        if not normalized_symbols:
            print("binance kline ws error: at least one --symbol is required")
            return 1
        collector = BinanceKlineWebSocketCollector(
            db_path=db_path,
            symbols=normalized_symbols,
            interval=args.interval,
            max_streams_per_connection=args.max_streams_per_connection,
            gap_fill_on_reconnect=args.gap_fill_on_reconnect,
        )
        result = collector.run_forever()
        print(
            "binance kline ws stopped: "
            f"{result.items_synced} messages, interval={args.interval}"
        )
        return 0

    if args.command == "run-binance-futures-kline-ws":
        symbols = args.symbol or _futures_ws_symbols(args.top_usdt_limit)
        normalized_symbols = [symbol.upper() for symbol in symbols]
        if args.dry_run:
            symbol_text = ",".join(normalized_symbols) or "none"
            print(
                "binance futures kline ws ready: "
                f"{symbol_text} interval={args.interval}"
            )
            return 0
        if not normalized_symbols:
            print("binance futures kline ws error: at least one --symbol is required")
            return 1

        def fill_futures_gaps(
            connection: sqlite3.Connection,
            symbols: list[str],
            start_ts_utc: str,
            end_ts_utc: str,
        ):
            return fill_binance_futures_1m_gaps(
                connection,
                symbols=symbols,
                start_ts_utc=start_ts_utc,
                end_ts_utc=end_ts_utc,
            )

        collector = BinanceKlineWebSocketCollector(
            db_path=db_path,
            symbols=normalized_symbols,
            interval=args.interval,
            max_streams_per_connection=args.max_streams_per_connection,
            gap_fill_on_reconnect=args.gap_fill_on_reconnect,
            gap_filler=fill_futures_gaps,
            ws_base_url="wss://fstream.binance.com/market",
            log_prefix="binance futures ws",
            message_handler=apply_binance_futures_kline_event,
        )
        result = collector.run_forever()
        print(
            "binance futures kline ws stopped: "
            f"{result.items_synced} messages, interval={args.interval}"
        )
        return 0

    if args.command == "add-alert-rule":
        with connect(db_path) as connection:
            AlertRuleRepository(connection).upsert(
                AlertRule(
                    name=args.name,
                    market=args.market,
                    symbol=args.symbol.upper(),
                    metric=args.metric,
                    operator=args.operator,
                    threshold=args.threshold,
                )
            )
        print(f"alert rule saved: {args.name}")
        return 0

    if args.command == "run-alerts":
        triggered_at_utc = args.triggered_at_utc or datetime.now(tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        with connect(db_path) as connection:
            result = evaluate_alert_rules(
                connection,
                triggered_at_utc=triggered_at_utc,
            )
        print(
            "alerts evaluated: "
            f"{result.rules_checked} rules, {result.events_created} events"
        )
        return 0

    if args.command == "list-alert-events":
        with connect(db_path) as connection:
            events = AlertEventRepository(connection).list_recent(limit=args.limit)
        print(
            json.dumps(
                [
                    {
                        "event_id": event.event_id,
                        "rule_name": event.rule_name,
                        "market": event.market,
                        "symbol": event.symbol,
                        "triggered_at_utc": event.triggered_at_utc,
                        "metric": event.metric,
                        "observed_value": event.observed_value,
                        "threshold": event.threshold,
                        "message": event.message,
                        "is_acknowledged": event.is_acknowledged,
                    }
                    for event in events
                ],
                ensure_ascii=False,
            )
        )
        return 0

    return 0


def _parse_utc_arg(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_utc_arg(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _top_futures_usdt_symbols(limit: int) -> list[str]:
    if limit <= 0:
        return []
    tickers = fetch_binance_futures_24hr_tickers()
    exchange_info = fetch_binance_futures_exchange_info()
    return select_top_futures_usdt_symbols(
        tickers,
        exchange_info,
        limit=limit,
    )


def _futures_ws_symbols(limit: int) -> list[str]:
    if limit <= 0:
        return []
    tickers = fetch_binance_futures_24hr_tickers()
    exchange_info = fetch_binance_futures_exchange_info()
    total_symbols = select_top_futures_usdt_symbols(
        tickers,
        exchange_info,
        limit=limit,
    )
    tradefi_symbols = select_futures_tradefi_symbols(
        tickers,
        exchange_info,
        limit=limit,
    )
    return list(dict.fromkeys([*total_symbols, *tradefi_symbols]))


def _sync_crypto_futures_boards(
    connection: sqlite3.Connection,
    *,
    tickers: list[dict[str, object]],
    exchange_info: dict[str, dict[str, object]],
    snapshot_ts_utc: str,
    trade_date_local: str,
    board_limit: int,
) -> CollectorResult:
    total_symbols = select_top_futures_usdt_symbols(
        tickers,
        exchange_info,
        limit=board_limit,
    )
    tradefi_symbols = select_futures_tradefi_symbols(
        tickers,
        exchange_info,
        limit=board_limit,
    )
    symbols_to_store = sorted(set(total_symbols) | set(tradefi_symbols))
    ticker_by_symbol = {str(item.get("symbol", "")).upper(): item for item in tickers}

    instruments = InstrumentRepository(connection)
    snapshots = MarketSnapshotRepository(connection)
    instrument_ids_by_symbol: dict[str, int] = {}
    for symbol in symbols_to_store:
        info = exchange_info.get(symbol)
        ticker = ticker_by_symbol.get(symbol)
        if info is None or ticker is None:
            continue
        instrument = binance_futures_symbol_to_instrument(info)
        instrument_id = instruments.upsert(instrument)
        instrument_ids_by_symbol[symbol] = instrument_id
        snapshots.upsert(
            parse_binance_futures_24hr_ticker_snapshot(
                instrument_id=instrument_id,
                instrument=instrument,
                ticker=ticker,
                snapshot_ts_utc=snapshot_ts_utc,
                trade_date_local=trade_date_local,
            )
        )

    WatchlistRepository(connection).replace(
        "CRYPTO_FUTURES_TRADFI",
        [
            WatchlistEntry(
                instrument_id=instrument_ids_by_symbol[symbol],
                sort_order=index,
            )
            for index, symbol in enumerate(tradefi_symbols, start=1)
            if symbol in instrument_ids_by_symbol
        ],
    )
    ranking = RankingRepository(connection)
    total_count = ranking.refresh_turnover_board(
        board_name="CRYPTO_FUTURES_TURNOVER_TOP50",
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        market="CRYPTO_FUTURES",
        instrument_type="crypto_futures",
        limit=board_limit,
    )
    tradefi_count = ranking.refresh_turnover_board(
        board_name="CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50",
        snapshot_ts_utc=snapshot_ts_utc,
        trade_date_local=trade_date_local,
        market="CRYPTO_FUTURES",
        instrument_type="crypto_futures",
        limit=board_limit,
        watchlist_name="CRYPTO_FUTURES_TRADFI",
    )
    return CollectorResult(
        source_name="binance_futures",
        items_synced=len(tickers),
        metadata={
            "stored_symbols": symbols_to_store,
            "total_board_rows": total_count,
            "tradefi_board_rows": tradefi_count,
        },
    )


def _crypto_symbols_for_aggregation(
    connection: sqlite3.Connection,
    *,
    symbols: list[str],
    limit: int,
) -> list[str]:
    if symbols:
        return [symbol.upper() for symbol in symbols]
    rows = connection.execute(
        """
        WITH latest_snapshot AS (
            SELECT instrument_id, MAX(snapshot_ts_utc) AS snapshot_ts_utc
            FROM market_snapshot
            GROUP BY instrument_id
        )
        SELECT instrument.symbol
        FROM instrument
        JOIN latest_snapshot
            ON latest_snapshot.instrument_id = instrument.instrument_id
        JOIN market_snapshot
            ON market_snapshot.instrument_id = latest_snapshot.instrument_id
            AND market_snapshot.snapshot_ts_utc = latest_snapshot.snapshot_ts_utc
        WHERE instrument.market = 'CRYPTO'
            AND instrument.instrument_type = 'crypto'
            AND instrument.is_active = 1
        ORDER BY market_snapshot.turnover_raw DESC, instrument.symbol
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if rows:
        return [str(row["symbol"]).upper() for row in rows]
    rows = connection.execute(
        """
        SELECT symbol
        FROM instrument
        WHERE market = 'CRYPTO'
            AND instrument_type = 'crypto'
            AND is_active = 1
        ORDER BY symbol
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(row["symbol"]).upper() for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())
