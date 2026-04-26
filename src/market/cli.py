from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from market.api import serve_api
from market.binance import sync_binance_klines
from market.db import connect, init_database
from market.repositories import RankingRepository
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
