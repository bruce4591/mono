from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class KlineLabelConfig:
    min_range_pct: float = 0.0015
    min_wick_ratio: float = 0.55
    rebound_ratio: float = 0.50
    trend_close_ratio: float = 0.25


def load_binance_klines_csv(path: str | Path) -> list[dict[str, float]]:
    candles: list[dict[str, float]] = []
    with open(path, newline="", encoding="utf-8") as handle:
        first = handle.readline()
        handle.seek(0)
        if first.lstrip().startswith("open_time") or first.lstrip().startswith("timestamp"):
            rows: Iterable[dict[str, str]] = csv.DictReader(handle)
        else:
            fieldnames = [
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_asset_volume",
                "number_of_trades",
                "taker_buy_base_asset_volume",
                "taker_buy_quote_asset_volume",
                "ignore",
            ]
            rows = csv.DictReader(handle, fieldnames=fieldnames)
        for row in rows:
            timestamp = parse_binance_timestamp(row.get("open_time") or row.get("timestamp") or "")
            candles.append(
                {
                    "timestamp": timestamp,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "quote_volume": float(row.get("quote_asset_volume") or row.get("quote_volume") or 0.0),
                    "trade_count": float(row.get("number_of_trades") or row.get("trade_count") or 0.0),
                    "taker_buy_quote_volume": float(
                        row.get("taker_buy_quote_asset_volume") or row.get("taker_buy_quote_volume") or 0.0
                    ),
                }
            )
    return candles


def label_candle(candle: dict[str, float], config: KlineLabelConfig | None = None) -> dict[str, object]:
    cfg = config or KlineLabelConfig()
    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    price_range = high - low
    if open_price <= 0.0 or price_range <= 0.0:
        stage = "normal"
        direction = None
        lower_ratio = upper_ratio = rebound = range_pct = 0.0
    else:
        body_top = max(open_price, close)
        body_bottom = min(open_price, close)
        lower_ratio = (body_bottom - low) / price_range
        upper_ratio = (high - body_top) / price_range
        lower_rebound = (close - low) / price_range
        upper_rebound = (high - close) / price_range
        range_pct = price_range / open_price
        stage = "normal"
        direction = None
        rebound = 0.0
        if range_pct >= cfg.min_range_pct and lower_ratio >= cfg.min_wick_ratio:
            direction = "down_flush"
            rebound = lower_rebound
            stage = "down_wick" if lower_rebound >= cfg.rebound_ratio else "trend_break"
        elif range_pct >= cfg.min_range_pct and upper_ratio >= cfg.min_wick_ratio:
            direction = "up_squeeze"
            rebound = upper_rebound
            stage = "up_wick" if upper_rebound >= cfg.rebound_ratio else "trend_break"
        elif range_pct >= cfg.min_range_pct and (lower_rebound <= cfg.trend_close_ratio or upper_rebound <= cfg.trend_close_ratio):
            stage = "trend_break"
            direction = "down_flush" if close <= open_price else "up_squeeze"

    row: dict[str, object] = dict(candle)
    row.update(
        {
            "kline_stage": stage,
            "kline_direction": direction,
            "kline_range_pct": range_pct,
            "kline_lower_wick_ratio": lower_ratio,
            "kline_upper_wick_ratio": upper_ratio,
            "kline_rebound_ratio": rebound,
        }
    )
    return row


def label_candles(
    candles: Iterable[dict[str, float]],
    config: KlineLabelConfig | None = None,
) -> Iterable[dict[str, object]]:
    cfg = config or KlineLabelConfig()
    for candle in candles:
        yield label_candle(candle, cfg)


def parse_binance_timestamp(value: str) -> float:
    if not value:
        return 0.0
    try:
        raw = float(value)
    except ValueError:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).timestamp()
    if raw > 10_000_000_000_000:
        return raw / 1_000_000.0
    if raw > 10_000_000_000:
        return raw / 1000.0
    return raw


def write_labels_jsonl(rows: Iterable[dict[str, object]], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True))
            handle.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Label Binance 1m kline candles for pin-stage threshold learning.")
    parser.add_argument("--kline-csv", required=True)
    parser.add_argument("--output-labels", required=True)
    parser.add_argument("--min-range-pct", type=float, default=0.0015)
    parser.add_argument("--min-wick-ratio", type=float, default=0.55)
    parser.add_argument("--rebound-ratio", type=float, default=0.50)
    parser.add_argument("--trend-close-ratio", type=float, default=0.25)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config = KlineLabelConfig(
        min_range_pct=args.min_range_pct,
        min_wick_ratio=args.min_wick_ratio,
        rebound_ratio=args.rebound_ratio,
        trend_close_ratio=args.trend_close_ratio,
    )
    rows = list(label_candles(load_binance_klines_csv(args.kline_csv), config))
    write_labels_jsonl(rows, args.output_labels)
    print(f"labels: {args.output_labels} ({len(rows)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
