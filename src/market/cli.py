from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from market.alerts import evaluate_alert_rules
from market.api import serve_api
from market.binance import (
    fetch_top_binance_usdt_symbols,
    sync_binance_daily_bars,
    sync_binance_klines,
)
from market.collectors.binance import BinanceCollector
from market.collectors.base import run_collector_job
from market.db import connect, init_database
from market.models import AlertRule
from market.realtime import apply_binance_ticker_event
from market.repositories import AlertEventRepository, AlertRuleRepository, RankingRepository
from market.sample_data import seed_sample_data
from market.settings import load_settings
from market.watchlists import sync_watchlist_from_file


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
    sync_crypto_board.add_argument("--interval", default="15m")
    sync_crypto_board.add_argument("--limit", type=int, default=96)
    sync_crypto_board.add_argument("--board-name", default="CRYPTO_TURNOVER_TOP50")
    sync_crypto_board.add_argument("--board-limit", type=int, default=50)
    sync_crypto_board.add_argument("--top-usdt-limit", type=int, default=50)
    sync_crypto_board.add_argument("--snapshot-ts-utc", default=None)
    sync_crypto_board.add_argument("--trade-date-local", default=None)
    sync_crypto_board.add_argument("--dry-run", action="store_true")

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

    apply_ticker = subparsers.add_parser(
        "apply-binance-ticker-event",
        help="Apply one Binance ticker WebSocket event JSON payload to market_snapshot",
    )
    apply_ticker.add_argument("--db-path", type=Path, default=None)
    apply_ticker.add_argument("--path", type=Path, required=True)

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

    if args.command == "sync-crypto-board":
        symbols = args.symbol or fetch_top_binance_usdt_symbols(limit=args.top_usdt_limit)
        now = datetime.now(tz=UTC)
        snapshot_ts_utc = args.snapshot_ts_utc or now.strftime("%Y-%m-%dT%H:%M:%SZ")
        trade_date_local = args.trade_date_local or now.date().isoformat()
        if args.dry_run:
            print(
                "crypto board sync ready: "
                f"{','.join(symbol.upper() for symbol in symbols)} "
                f"{args.interval} limit={args.limit} board={args.board_name}"
            )
            return 0
        with connect(db_path) as connection:
            checkpoint = ",".join(symbol.upper() for symbol in symbols) + f":{args.interval}"
            ranking_count = 0

            def sync_and_rank():
                nonlocal ranking_count
                result = BinanceCollector().sync_intraday_bars(
                    connection,
                    [symbol.upper() for symbol in symbols],
                    interval=args.interval,
                    limit=args.limit,
                )
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
            f"{len(symbols)} symbols, {ranking_count} ranking rows, "
            f"snapshot={snapshot_ts_utc}"
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


if __name__ == "__main__":
    raise SystemExit(main())
