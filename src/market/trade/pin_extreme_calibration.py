from __future__ import annotations

import argparse
import csv
import gzip
import heapq
import json
import math
import zipfile
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Literal

from market.trade.pin_stage_replay import BookMetrics, OrderBook, empty_book_metrics, imbalance


Direction = Literal["down_flush", "up_squeeze"]


@dataclass(frozen=True)
class PrefilterRange:
    start_ts: float
    end_ts: float
    source_window_seconds: float
    move_pct: float
    range_pct: float
    direction: Direction


@dataclass(frozen=True)
class ShockCandidate:
    source: str
    date: str
    symbol: str
    window_seconds: float
    start_ts: float
    end_ts: float
    direction: Direction
    start_price: float
    end_price: float
    low_price: float
    high_price: float
    move_pct: float
    abs_move_pct: float
    range_pct: float
    trade_count: int
    buy_notional: float
    sell_notional: float
    total_notional: float
    trade_imbalance: float
    score: float
    book_start: BookMetrics | None = None
    book_end: BookMetrics | None = None
    book_depth_drop_25: float | None = None
    book_imbalance_change_25: float | None = None

    def overlaps(self, other: "ShockCandidate", min_gap_seconds: float) -> bool:
        if self.source != other.source:
            return False
        return not (
            self.end_ts + min_gap_seconds < other.start_ts
            or other.end_ts + min_gap_seconds < self.start_ts
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "date": self.date,
            "symbol": self.symbol,
            "window_seconds": self.window_seconds,
            "start_time": isoformat_seconds(self.start_ts),
            "end_time": isoformat_seconds(self.end_ts),
            "direction": self.direction,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "low_price": self.low_price,
            "high_price": self.high_price,
            "move_pct": self.move_pct,
            "abs_move_pct": self.abs_move_pct,
            "range_pct": self.range_pct,
            "trade_count": self.trade_count,
            "buy_notional": self.buy_notional,
            "sell_notional": self.sell_notional,
            "total_notional": self.total_notional,
            "trade_imbalance": self.trade_imbalance,
            "score": self.score,
            "book_start": book_to_dict(self.book_start),
            "book_end": book_to_dict(self.book_end),
            "book_depth_drop_25": self.book_depth_drop_25,
            "book_imbalance_change_25": self.book_imbalance_change_25,
        }


@dataclass
class RollingTradeWindow:
    seconds: float
    trades: deque[tuple[float, float, float, str]]
    min_prices: deque[tuple[float, float]]
    max_prices: deque[tuple[float, float]]
    buy_notional: float = 0.0
    sell_notional: float = 0.0

    @classmethod
    def create(cls, seconds: float) -> "RollingTradeWindow":
        return cls(seconds=seconds, trades=deque(), min_prices=deque(), max_prices=deque())

    def add(self, timestamp: float, price: float, notional: float, side: str) -> None:
        self.trades.append((timestamp, price, notional, side))
        if side == "Buy":
            self.buy_notional += notional
        else:
            self.sell_notional += notional

        while self.min_prices and self.min_prices[-1][1] >= price:
            self.min_prices.pop()
        self.min_prices.append((timestamp, price))

        while self.max_prices and self.max_prices[-1][1] <= price:
            self.max_prices.pop()
        self.max_prices.append((timestamp, price))

        cutoff = timestamp - self.seconds
        while self.trades and self.trades[0][0] < cutoff:
            old_timestamp, _old_price, old_notional, old_side = self.trades.popleft()
            if old_side == "Buy":
                self.buy_notional -= old_notional
            else:
                self.sell_notional -= old_notional
        while self.min_prices and self.min_prices[0][0] < cutoff:
            self.min_prices.popleft()
        while self.max_prices and self.max_prices[0][0] < cutoff:
            self.max_prices.popleft()

    def candidate(self, *, source: str, date: str, symbol: str) -> ShockCandidate | None:
        if len(self.trades) < 2:
            return None
        start_ts, start_price, _start_notional, _start_side = self.trades[0]
        end_ts, end_price, _end_notional, _end_side = self.trades[-1]
        if start_price <= 0:
            return None

        low_price = self.min_prices[0][1]
        high_price = self.max_prices[0][1]
        move_pct = (end_price - start_price) / start_price
        abs_move_pct = abs(move_pct)
        range_pct = (high_price - low_price) / start_price
        total_notional = self.buy_notional + self.sell_notional
        trade_imbalance = imbalance(self.buy_notional, self.sell_notional)
        direction = infer_direction(move_pct, end_price, low_price, high_price)
        score = score_candidate(abs_move_pct, range_pct, total_notional, trade_imbalance)

        return ShockCandidate(
            source=source,
            date=date,
            symbol=symbol,
            window_seconds=self.seconds,
            start_ts=start_ts,
            end_ts=end_ts,
            direction=direction,
            start_price=start_price,
            end_price=end_price,
            low_price=low_price,
            high_price=high_price,
            move_pct=move_pct,
            abs_move_pct=abs_move_pct,
            range_pct=range_pct,
            trade_count=len(self.trades),
            buy_notional=self.buy_notional,
            sell_notional=self.sell_notional,
            total_notional=total_notional,
            trade_imbalance=trade_imbalance,
            score=score,
        )


def scan_trade_file(
    path: str | Path,
    *,
    symbol: str,
    windows: Iterable[float],
    keep_per_window: int = 2_000,
    min_abs_move_pct: float = 0.0,
    prefilter_ranges: Iterable[PrefilterRange] | None = None,
) -> list[ShockCandidate]:
    trade_path = Path(path)
    date = date_from_trade_path(trade_path)
    window_states = {float(window): RollingTradeWindow.create(float(window)) for window in windows}
    heaps: dict[float, list[tuple[float, int, ShockCandidate]]] = {window: [] for window in window_states}
    ranges = sorted(prefilter_ranges or [], key=lambda item: item.start_ts)
    range_index = 0
    sequence = 0

    with gzip.open(trade_path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row["symbol"] != symbol:
                continue
            side = row["side"]
            if side not in {"Buy", "Sell"}:
                continue
            timestamp = float(row["timestamp"])
            price = float(row["price"])
            notional = price * float(row["size"])
            if ranges:
                while range_index < len(ranges) and ranges[range_index].end_ts < timestamp:
                    range_index += 1
                if range_index >= len(ranges):
                    break
                if timestamp < ranges[range_index].start_ts:
                    continue

            for window, state in window_states.items():
                state.add(timestamp, price, notional, side)
                candidate = state.candidate(source=str(trade_path), date=date, symbol=symbol)
                if candidate is None or candidate.abs_move_pct < min_abs_move_pct:
                    continue
                heap = heaps[window]
                entry = (candidate.score, sequence, candidate)
                sequence += 1
                if len(heap) < keep_per_window:
                    heapq.heappush(heap, entry)
                elif candidate.score > heap[0][0]:
                    heapq.heapreplace(heap, entry)

    candidates: list[ShockCandidate] = []
    for heap in heaps.values():
        candidates.extend(candidate for _score, _sequence, candidate in heap)
    return candidates


@dataclass
class CoarseBar:
    start_ts: float
    open: float
    high: float
    low: float
    close: float


def build_prefilter_ranges(
    path: str | Path,
    *,
    symbol: str,
    bar_seconds: Iterable[float],
    min_move_pct: float,
    min_range_pct: float,
    padding_seconds: float,
) -> list[PrefilterRange]:
    windows = sorted({float(item) for item in bar_seconds if float(item) > 0.0})
    bars: dict[float, dict[float, CoarseBar]] = {window: {} for window in windows}

    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row["symbol"] != symbol:
                continue
            timestamp = float(row["timestamp"])
            price = float(row["price"])
            for window in windows:
                bucket = math.floor(timestamp / window) * window
                bar = bars[window].get(bucket)
                if bar is None:
                    bars[window][bucket] = CoarseBar(bucket, price, price, price, price)
                else:
                    bar.high = max(bar.high, price)
                    bar.low = min(bar.low, price)
                    bar.close = price

    ranges: list[PrefilterRange] = []
    for window, window_bars in bars.items():
        for bar in window_bars.values():
            if bar.open <= 0.0:
                continue
            move_pct = (bar.close - bar.open) / bar.open
            range_pct = (bar.high - bar.low) / bar.open
            if abs(move_pct) < min_move_pct and range_pct < min_range_pct:
                continue
            direction = infer_direction(move_pct, bar.close, bar.low, bar.high)
            ranges.append(
                PrefilterRange(
                    start_ts=bar.start_ts - padding_seconds,
                    end_ts=bar.start_ts + window + padding_seconds,
                    source_window_seconds=window,
                    move_pct=move_pct,
                    range_pct=range_pct,
                    direction=direction,
                )
            )
    return sorted(ranges, key=lambda item: (item.start_ts, item.end_ts, item.source_window_seconds))


def scan_trade_files(
    paths: Iterable[str | Path],
    *,
    symbol: str,
    windows: Iterable[float],
    keep_per_window: int,
    min_abs_move_pct: float,
    prefilter_bar_seconds: Iterable[float] | None = None,
    prefilter_min_move_pct: float = 0.0,
    prefilter_min_range_pct: float = 0.0,
    prefilter_padding_seconds: float = 0.0,
) -> list[ShockCandidate]:
    candidates: list[ShockCandidate] = []
    for path in paths:
        prefilter_ranges = None
        if prefilter_bar_seconds:
            prefilter_ranges = build_prefilter_ranges(
                path,
                symbol=symbol,
                bar_seconds=prefilter_bar_seconds,
                min_move_pct=prefilter_min_move_pct,
                min_range_pct=prefilter_min_range_pct,
                padding_seconds=prefilter_padding_seconds,
            )
        candidates.extend(
            scan_trade_file(
                path,
                symbol=symbol,
                windows=windows,
                keep_per_window=keep_per_window,
                min_abs_move_pct=min_abs_move_pct,
                prefilter_ranges=prefilter_ranges,
            )
        )
    return candidates


def select_non_overlapping(
    candidates: Iterable[ShockCandidate],
    *,
    top_n: int,
    min_gap_seconds: float,
    window_seconds: float | None = None,
) -> list[ShockCandidate]:
    pool = [
        candidate
        for candidate in candidates
        if window_seconds is None or candidate.window_seconds == window_seconds
    ]
    selected: list[ShockCandidate] = []
    for candidate in sorted(pool, key=lambda item: (item.score, item.abs_move_pct), reverse=True):
        if any(candidate.overlaps(existing, min_gap_seconds) for existing in selected):
            continue
        selected.append(candidate)
        if len(selected) >= top_n:
            break
    return selected


def build_extreme_standard(
    candidates: Iterable[ShockCandidate],
    *,
    top_n: int,
    primary_window: float,
    min_gap_seconds: float = 60.0,
) -> dict[str, object]:
    candidate_list = list(candidates)
    windows = sorted({candidate.window_seconds for candidate in candidate_list})
    top_by_window: dict[str, list[dict[str, object]]] = {}
    for window in windows:
        selected = select_non_overlapping(
            candidate_list,
            top_n=top_n,
            min_gap_seconds=min_gap_seconds,
            window_seconds=window,
        )
        top_by_window[format_window(window)] = [candidate.to_dict() for candidate in selected]

    primary_selected = select_non_overlapping(
        candidate_list,
        top_n=max(top_n, 2),
        min_gap_seconds=min_gap_seconds,
        window_seconds=primary_window,
    )
    max_abs = primary_selected[0].abs_move_pct if primary_selected else 0.0
    second_abs = primary_selected[1].abs_move_pct if len(primary_selected) > 1 else max_abs
    max_score = primary_selected[0].score if primary_selected else 0.0
    second_score = primary_selected[1].score if len(primary_selected) > 1 else max_score

    return {
        "primary_resolution": format_window(primary_window),
        "reason": (
            build_standard_reason(primary_window)
        ),
        "standard": {
            "primary_window_seconds": primary_window,
            "max_abs_move_pct": max_abs,
            "second_abs_move_pct": second_abs,
            "max_score": max_score,
            "second_score": second_score,
            "candidate_count": len(candidate_list),
        },
        "top_by_window": top_by_window,
    }


def enrich_with_orderbook(
    candidates: Iterable[ShockCandidate],
    *,
    data_dir: str | Path,
    symbol: str,
) -> list[ShockCandidate]:
    data_path = Path(data_dir)
    grouped: dict[str, list[ShockCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.date, []).append(candidate)

    enriched: list[ShockCandidate] = []
    for date, day_candidates in grouped.items():
        orderbook_path = data_path / f"{date}_{symbol}_ob200.data.zip"
        if not orderbook_path.exists():
            enriched.extend(day_candidates)
            continue
        metrics_by_ts = snapshot_book_metrics(orderbook_path, sorted(target_times(day_candidates)))
        for candidate in day_candidates:
            start_book = metrics_by_ts.get(candidate.start_ts)
            end_book = metrics_by_ts.get(candidate.end_ts)
            enriched.append(add_book_summary(candidate, start_book, end_book))
    return enriched


def snapshot_book_metrics(path: str | Path, targets: list[float]) -> dict[float, BookMetrics]:
    if not targets:
        return {}
    remaining = deque(targets)
    captured: dict[float, BookMetrics] = {}
    book = OrderBook()
    last_metrics = empty_book_metrics()

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:
            return captured
        with archive.open(names[0]) as handle:
            for raw_line in handle:
                payload = json.loads(raw_line)
                timestamp = float(payload["ts"]) / 1000.0
                data = payload.get("data", {})
                if payload["type"] == "snapshot":
                    book.apply_snapshot(data.get("b", []), data.get("a", []))
                else:
                    book.apply_delta(data.get("b", []), data.get("a", []))
                last_metrics = book.metrics()

                while remaining and timestamp >= remaining[0]:
                    captured[remaining.popleft()] = last_metrics
                if not remaining:
                    break

    while remaining:
        captured[remaining.popleft()] = last_metrics
    return captured


def add_book_summary(
    candidate: ShockCandidate,
    start_book: BookMetrics | None,
    end_book: BookMetrics | None,
) -> ShockCandidate:
    if start_book is None or end_book is None:
        return candidate
    start_depth = directional_depth(start_book, candidate.direction)
    end_depth = directional_depth(end_book, candidate.direction)
    depth_drop = None
    if start_depth > 0:
        depth_drop = max(0.0, min(1.0, (start_depth - end_depth) / start_depth))
    return replace(
        candidate,
        book_start=start_book,
        book_end=end_book,
        book_depth_drop_25=depth_drop,
        book_imbalance_change_25=end_book.depth_imbalance_25 - start_book.depth_imbalance_25,
    )


def write_report_json(report: dict[str, object], path: str | Path) -> None:
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_report_markdown(report: dict[str, object], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Extreme Pin Standard",
        "",
        f"Primary resolution: `{report['primary_resolution']}`",
        "",
        str(report["reason"]),
        "",
        "## Standard",
        "",
    ]
    standard = report["standard"]
    if isinstance(standard, dict):
        lines.extend(
            [
                f"- Max abs move pct: `{standard['max_abs_move_pct']:.6f}`",
                f"- Second abs move pct: `{standard['second_abs_move_pct']:.6f}`",
                f"- Max score: `{standard['max_score']:.6f}`",
                f"- Second score: `{standard['second_score']:.6f}`",
                f"- Candidate count before non-overlap selection: `{standard['candidate_count']}`",
                "",
            ]
        )

    lines.append("## Top Candidates By Window")
    lines.append("")
    top_by_window = report["top_by_window"]
    if isinstance(top_by_window, dict):
        for window, rows in top_by_window.items():
            lines.append(f"### {window}")
            lines.append("")
            lines.append("| rank | end time | direction | abs move | score | start | end | notional | trade imbalance | book depth drop |")
            lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            for index, row in enumerate(rows, start=1):
                if not isinstance(row, dict):
                    continue
                depth_drop = row.get("book_depth_drop_25")
                depth_value = "" if depth_drop is None else f"{depth_drop:.3f}"
                lines.append(
                    "| "
                    f"{index} | {row['end_time']} | {row['direction']} | {row['abs_move_pct']:.6f} | "
                    f"{row['score']:.6f} | {row['start_price']:.2f} | {row['end_price']:.2f} | "
                    f"{row['total_notional']:.0f} | {row['trade_imbalance']:.3f} | {depth_value} |"
                )
            lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")


def discover_trade_paths(data_dir: str | Path, dates: Iterable[str], symbol: str) -> list[Path]:
    base = Path(data_dir)
    paths: list[Path] = []
    for date in dates:
        path = base / f"{symbol}{date}.csv.gz"
        if not path.exists():
            path = base / f"{symbol}{date.replace('-', '')}.csv.gz"
        if not path.exists():
            path = base / f"{symbol}{date}.csv.gz"
        if not path.exists():
            raise FileNotFoundError(f"Missing trade file for {date}: {path}")
        paths.append(path)
    return paths


def parse_windows(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def infer_direction(move_pct: float, end_price: float, low_price: float, high_price: float) -> Direction:
    if move_pct < 0:
        return "down_flush"
    if move_pct > 0:
        return "up_squeeze"
    low_distance = abs(end_price - low_price)
    high_distance = abs(high_price - end_price)
    return "down_flush" if low_distance <= high_distance else "up_squeeze"


def score_candidate(
    abs_move_pct: float,
    range_pct: float,
    total_notional: float,
    trade_imbalance: float,
) -> float:
    move_component = max(abs_move_pct, range_pct * 0.65)
    notional_component = min(total_notional / 100_000_000.0, 1.0)
    imbalance_component = abs(trade_imbalance)
    return move_component * (1.0 + 0.35 * notional_component + 0.20 * imbalance_component)


def date_from_trade_path(path: Path) -> str:
    name = path.name
    for token in name.replace(".csv.gz", "").split("BTCUSDT"):
        if len(token) == 10 and token[4] == "-" and token[7] == "-":
            return token
    stem = name.replace(".csv.gz", "")
    if stem.startswith("BTCUSDT") and len(stem) >= 18:
        raw = stem[len("BTCUSDT") : len("BTCUSDT") + 10]
        if raw[4] == "-" and raw[7] == "-":
            return raw
    raise ValueError(f"Cannot infer date from {path}")


def target_times(candidates: Iterable[ShockCandidate]) -> Iterator[float]:
    for candidate in candidates:
        yield candidate.start_ts
        yield candidate.end_ts


def directional_depth(book: BookMetrics, direction: Direction) -> float:
    return book.bid_depth_25 if direction == "down_flush" else book.ask_depth_25


def book_to_dict(book: BookMetrics | None) -> dict[str, float] | None:
    if book is None:
        return None
    return {
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "mid": book.mid,
        "spread": book.spread,
        "bid_depth_5": book.bid_depth_5,
        "ask_depth_5": book.ask_depth_5,
        "bid_depth_25": book.bid_depth_25,
        "ask_depth_25": book.ask_depth_25,
        "depth_imbalance_5": book.depth_imbalance_5,
        "depth_imbalance_25": book.depth_imbalance_25,
        "bid_depth_10bp": book.bid_depth_10bp,
        "ask_depth_10bp": book.ask_depth_10bp,
    }


def format_window(window: float) -> str:
    if float(window).is_integer():
        return f"{int(window)}s"
    return f"{window:g}s"


def build_standard_reason(primary_window: float) -> str:
    if primary_window == 1.0:
        return (
            "Use 1s as the microstructure reference; execution decisions should still "
            "confirm against the longer trigger windows to reduce noise."
        )
    return (
        f"Use {format_window(primary_window)} as the primary execution reference; "
        "1s is retained as microstructure context, 15s/30s/45s/60s capture fast wick pushes, "
        "and 120s/180s/240s capture slow lower/upper-wick pushes."
    )


def isoformat_seconds(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build BTCUSDT extreme pin standards from historical trade/OB data.")
    parser.add_argument("--data-dir", default="by_data")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--dates",
        default="2025-10-09,2025-10-10,2025-10-11,2026-02-04,2026-02-05,2026-02-06",
    )
    parser.add_argument("--windows", default="1,5,15,30,45,60,120,180,240")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--keep-per-window", type=int, default=4000)
    parser.add_argument("--min-gap-seconds", type=float, default=60.0)
    parser.add_argument("--min-abs-move-pct", type=float, default=0.001)
    parser.add_argument("--primary-window", type=float, default=5.0)
    parser.add_argument("--prefilter-bar-seconds", default="60,180")
    parser.add_argument("--prefilter-min-move-pct", type=float, default=0.004)
    parser.add_argument("--prefilter-min-range-pct", type=float, default=0.006)
    parser.add_argument("--prefilter-padding-seconds", type=float, default=90.0)
    parser.add_argument("--disable-prefilter", action="store_true")
    parser.add_argument("--skip-orderbook", action="store_true")
    parser.add_argument("--output-json", default="btcusdt_extreme_standard.json")
    parser.add_argument("--output-md", default="btcusdt_extreme_standard.md")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dates = [item.strip() for item in args.dates.split(",") if item.strip()]
    windows = parse_windows(args.windows)
    trade_paths = discover_trade_paths(args.data_dir, dates, args.symbol)
    candidates = scan_trade_files(
        trade_paths,
        symbol=args.symbol,
        windows=windows,
        keep_per_window=args.keep_per_window,
        min_abs_move_pct=args.min_abs_move_pct,
        prefilter_bar_seconds=None if args.disable_prefilter else parse_windows(args.prefilter_bar_seconds),
        prefilter_min_move_pct=args.prefilter_min_move_pct,
        prefilter_min_range_pct=args.prefilter_min_range_pct,
        prefilter_padding_seconds=args.prefilter_padding_seconds,
    )
    selected_for_enrichment: list[ShockCandidate] = []
    for window in windows:
        selected_for_enrichment.extend(
            select_non_overlapping(
                candidates,
                top_n=args.top,
                min_gap_seconds=args.min_gap_seconds,
                window_seconds=window,
            )
        )
    if not args.skip_orderbook:
        selected_for_enrichment = enrich_with_orderbook(
            selected_for_enrichment,
            data_dir=args.data_dir,
            symbol=args.symbol,
        )
        enriched_lookup = {
            (item.source, item.window_seconds, item.start_ts, item.end_ts): item
            for item in selected_for_enrichment
        }
        candidates = [
            enriched_lookup.get((item.source, item.window_seconds, item.start_ts, item.end_ts), item)
            for item in candidates
        ]

    report = build_extreme_standard(
        candidates,
        top_n=args.top,
        primary_window=args.primary_window,
        min_gap_seconds=args.min_gap_seconds,
    )
    write_report_json(report, args.output_json)
    write_report_markdown(report, args.output_md)
    print(f"trade files: {len(trade_paths)}")
    print(f"candidates kept: {len(candidates)}")
    print(f"json: {args.output_json}")
    print(f"markdown: {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
