from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sys
import zipfile
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from html import escape
from pathlib import Path
from typing import Iterable, Iterator, Literal


Direction = Literal["down_flush", "up_squeeze"]
TradeSide = Literal["Buy", "Sell"]

DEFAULT_FEATURE_WINDOWS = (1.0, 5.0, 15.0, 30.0, 45.0, 60.0, 120.0, 180.0, 240.0)
DEFAULT_TRIGGER_WINDOWS = (5.0, 15.0, 30.0, 45.0, 60.0, 120.0, 180.0, 240.0)
DEFAULT_SHOCK_MOVE_PCT = 0.006
DEFAULT_EXTREME_MOVE_PCT = 0.009
DEFAULT_SHOCK_MOVE_BY_WINDOW = (
    (5.0, 0.006),
    (15.0, 0.007),
    (30.0, 0.0075),
    (45.0, 0.008),
    (60.0, 0.008),
    (120.0, 0.0085),
    (180.0, 0.009),
    (240.0, 0.0095),
)
DEFAULT_EXTREME_MOVE_BY_WINDOW = (
    (5.0, 0.009),
    (15.0, 0.0105),
    (30.0, 0.01125),
    (45.0, 0.012),
    (60.0, 0.012),
    (120.0, 0.013),
    (180.0, 0.014),
    (240.0, 0.015),
)


class Stage(str, Enum):
    NORMAL = "normal"
    PRE_SHOCK = "pre_shock"
    SHOCK_NOW = "shock_now"
    EXTREME_FLUSH = "extreme_flush"
    REBOUND_START = "rebound_start"
    REBOUND_CONFIRMED = "rebound_confirmed"
    TREND_BREAK = "trend_break"


@dataclass(frozen=True)
class TradeEvent:
    timestamp: float
    symbol: str
    side: TradeSide
    size: float
    price: float

    @property
    def notional(self) -> float:
        return self.price * self.size


@dataclass(frozen=True)
class OrderBookEvent:
    timestamp: float
    event_type: str
    bids: list[tuple[str, str]]
    asks: list[tuple[str, str]]


@dataclass(frozen=True)
class BookMetrics:
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    bid_depth_5: float
    ask_depth_5: float
    bid_depth_25: float
    ask_depth_25: float
    depth_imbalance_5: float
    depth_imbalance_25: float
    bid_depth_10bp: float
    ask_depth_10bp: float


@dataclass(frozen=True)
class TradeWindowMetrics:
    start_price: float
    end_price: float
    low_price: float
    high_price: float
    price_move_pct: float
    range_pct: float
    trade_count: int
    buy_notional: float
    sell_notional: float
    total_notional: float
    trade_imbalance: float
    trades_per_second: float


@dataclass(frozen=True)
class EntryOrderPlan:
    entry_side: Literal["long", "short"]
    order_action: Literal["buy", "sell"]
    allocation_ratio: float
    total_notional: float
    slices: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class WindowSignal:
    window_seconds: float
    metrics: TradeWindowMetrics
    direction: Direction
    directional_move_pct: float
    shock_threshold_pct: float
    extreme_threshold_pct: float
    score: float

    @property
    def is_shock(self) -> bool:
        return self.directional_move_pct >= self.shock_threshold_pct

    @property
    def is_extreme(self) -> bool:
        return self.directional_move_pct >= self.extreme_threshold_pct


@dataclass(frozen=True)
class StageEvent:
    timestamp: float
    symbol: str
    stage: Stage
    direction: Direction | None
    price: float
    size: float
    side: str
    started_at: float | None
    ended_at: float | None
    entry_candidate_price: float | None
    strength: float
    trade_notional: float
    trade_imbalance: float
    book_imbalance: float
    depth_drop: float
    rebound_ratio: float
    trade_count: int
    buy_notional: float
    sell_notional: float
    best_bid: float | None = None
    best_ask: float | None = None
    mid_price: float | None = None
    spread: float | None = None
    book_timestamp: float | None = None
    book_age_ms: float | None = None
    trade_outside_book: Literal["below_bid", "above_ask"] | None = None
    trade_book_gap_bps: float | None = None
    trigger_window_seconds: float | None = None
    trade_windows: dict[str, dict[str, float]] | None = None
    slow_wick_stage: Literal["15m_lower_wick_candidate", "15m_upper_wick_candidate", "slow_rebound_confirmed"] | None = None
    slow_wick_candidate: bool = False
    slow_wick_rebound_confirmed: bool = False
    slow_wick_direction: Direction | None = None
    slow_wick_timestamp: float | None = None
    slow_wick_window_seconds: float | None = None
    slow_wick_open: float | None = None
    slow_wick_high: float | None = None
    slow_wick_low: float | None = None
    slow_wick_close: float | None = None
    slow_wick_range_pct: float = 0.0
    slow_wick_lower_ratio: float = 0.0
    slow_wick_upper_ratio: float = 0.0
    slow_wick_rebound_ratio: float = 0.0
    slow_wick_prev_low: float | None = None
    slow_wick_breaks_prev_low: bool | None = None
    slow_wick_ema: float | None = None
    slow_wick_close_above_ema: bool | None = None
    slow_wick_vwap: float | None = None
    slow_wick_close_above_vwap: bool | None = None
    slow_wick_trend_filter_passed: bool | None = None
    stage_changed: bool = False
    entry_order_plan: EntryOrderPlan | None = None

    def to_label(self) -> dict[str, object]:
        order_plan = self.entry_order_plan
        trade_windows = self.trade_windows or {}
        row: dict[str, object] = {
            "timestamp": isoformat_seconds(self.timestamp),
            "timestamp_ms": int(round(self.timestamp * 1000)),
            "symbol": self.symbol,
            "stage": self.stage.value,
            "direction": self.direction,
            "price": self.price,
            "side": self.side,
            "size": self.size,
            "started_at": isoformat_seconds(self.started_at) if self.started_at is not None else None,
            "ended_at": isoformat_seconds(self.ended_at) if self.ended_at is not None else None,
            "entry_candidate_price": self.entry_candidate_price,
            "strength": self.strength,
            "trade_notional": self.trade_notional,
            "trade_imbalance": self.trade_imbalance,
            "book_imbalance": self.book_imbalance,
            "depth_drop": self.depth_drop,
            "rebound_ratio": self.rebound_ratio,
            "trade_count": self.trade_count,
            "buy_notional": self.buy_notional,
            "sell_notional": self.sell_notional,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "book_timestamp": isoformat_seconds(self.book_timestamp) if self.book_timestamp is not None else None,
            "book_age_ms": self.book_age_ms,
            "trade_outside_book": self.trade_outside_book,
            "trade_book_gap_bps": self.trade_book_gap_bps,
            "trigger_window_seconds": self.trigger_window_seconds,
            "trade_windows": trade_windows,
            "slow_wick_stage": self.slow_wick_stage,
            "slow_wick_candidate": self.slow_wick_candidate,
            "slow_wick_rebound_confirmed": self.slow_wick_rebound_confirmed,
            "slow_wick_direction": self.slow_wick_direction,
            "slow_wick_timestamp": isoformat_seconds(self.slow_wick_timestamp) if self.slow_wick_timestamp is not None else None,
            "slow_wick_window_seconds": self.slow_wick_window_seconds,
            "slow_wick_open": self.slow_wick_open,
            "slow_wick_high": self.slow_wick_high,
            "slow_wick_low": self.slow_wick_low,
            "slow_wick_close": self.slow_wick_close,
            "slow_wick_range_pct": self.slow_wick_range_pct,
            "slow_wick_lower_ratio": self.slow_wick_lower_ratio,
            "slow_wick_upper_ratio": self.slow_wick_upper_ratio,
            "slow_wick_rebound_ratio": self.slow_wick_rebound_ratio,
            "slow_wick_prev_low": self.slow_wick_prev_low,
            "slow_wick_breaks_prev_low": self.slow_wick_breaks_prev_low,
            "slow_wick_ema": self.slow_wick_ema,
            "slow_wick_close_above_ema": self.slow_wick_close_above_ema,
            "slow_wick_vwap": self.slow_wick_vwap,
            "slow_wick_close_above_vwap": self.slow_wick_close_above_vwap,
            "slow_wick_trend_filter_passed": self.slow_wick_trend_filter_passed,
            "stage_changed": self.stage_changed,
            "entry_side": order_plan.entry_side if order_plan is not None else None,
            "order_action": order_plan.order_action if order_plan is not None else None,
            "entry_order_allocation_ratio": order_plan.allocation_ratio if order_plan is not None else 0.0,
            "entry_order_notional": order_plan.total_notional if order_plan is not None else 0.0,
            "entry_order_slices": list(order_plan.slices) if order_plan is not None else [],
        }
        for window_label, metrics in trade_windows.items():
            suffix = window_label.replace(".", "_")
            row[f"trade_move_pct_{suffix}"] = metrics.get("price_move_pct", 0.0)
            row[f"trade_range_pct_{suffix}"] = metrics.get("range_pct", 0.0)
            row[f"trade_notional_{suffix}"] = metrics.get("total_notional", 0.0)
            row[f"trade_imbalance_{suffix}"] = metrics.get("trade_imbalance", 0.0)
            row[f"trade_count_{suffix}"] = metrics.get("trade_count", 0.0)
        return row


@dataclass(frozen=True)
class CandleBar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class PinStageConfig:
    window_seconds: float = 5.0
    feature_window_seconds: tuple[float, ...] = DEFAULT_FEATURE_WINDOWS
    trigger_window_seconds: tuple[float, ...] = DEFAULT_TRIGGER_WINDOWS
    min_window_trades: int = 3
    pre_shock_move_pct: float = 0.0025
    pre_shock_move_pct_by_window: tuple[tuple[float, float], ...] = ()
    shock_move_pct: float = DEFAULT_SHOCK_MOVE_PCT
    extreme_move_pct: float = DEFAULT_EXTREME_MOVE_PCT
    shock_move_pct_by_window: tuple[tuple[float, float], ...] = DEFAULT_SHOCK_MOVE_BY_WINDOW
    extreme_move_pct_by_window: tuple[tuple[float, float], ...] = DEFAULT_EXTREME_MOVE_BY_WINDOW
    rebound_start_ratio: float = 0.25
    rebound_confirm_ratio: float = 0.55
    trend_break_seconds: float = 90.0
    min_trade_notional: float = 100_000.0
    min_trade_notional_by_window: tuple[tuple[float, float], ...] = ()
    high_imbalance: float = 0.55
    depth_drop_reference: float = 40.0
    strength_move_reference_pct: float = 0.012
    strength_notional_reference: float = 80_000_000.0
    strength_move_reference_by_window_pct: tuple[tuple[float, float], ...] = ()
    strength_notional_reference_by_window: tuple[tuple[float, float], ...] = ()
    stage_reset_seconds: float = 180.0
    min_entry_order_notional: float = 1_000.0
    max_entry_order_notional: float = 10_000.0
    entry_order_curve_gamma: float = 1.5
    rebound_start_order_scale: float = 0.0
    rebound_confirmed_order_scale: float = 1.0
    max_book_lag_seconds: float = 0.50
    slow_wick_window_seconds: float = 900.0
    slow_wick_min_range_pct: float = 0.0015
    slow_wick_min_ratio: float = 0.55
    slow_wick_rebound_ratio: float = 0.50
    slow_wick_trend_lookback_candles: int = 4
    slow_wick_ema_period: int = 9
    slow_wick_vwap_lookback_candles: int = 4


@dataclass
class ActiveShock:
    direction: Direction
    started_at: float
    anchor_price: float
    shock_price: float
    extreme_price: float
    peak_trade_notional: float
    peak_strength: float
    baseline_depth_25: float | None


@dataclass(frozen=True)
class TradeBookMatch:
    outside: Literal["below_bid", "above_ask"] | None
    gap_bps: float | None
    book_age_ms: float | None


def apply_extreme_standard(config: PinStageConfig, path: str | Path) -> PinStageConfig:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"Extreme standard must be a JSON object: {path}")
    standard = report.get("standard")
    if not isinstance(standard, dict):
        raise ValueError(f"Extreme standard is missing standard object: {path}")

    updates: dict[str, object] = {}
    move_reference = positive_float(standard.get("max_abs_move_pct")) or positive_float(
        standard.get("second_abs_move_pct")
    )
    if move_reference is not None:
        updates["strength_move_reference_pct"] = move_reference

    primary_window = report.get("primary_resolution")
    if not isinstance(primary_window, str):
        primary_window = format_window_label(standard.get("primary_window_seconds"))
    notional_reference = max_top_notional(report.get("top_by_window"), primary_window)
    if notional_reference is not None:
        updates["strength_notional_reference"] = notional_reference

    move_references = top_window_references(report.get("top_by_window"), "abs_move_pct")
    if move_references:
        updates["strength_move_reference_by_window_pct"] = tuple(move_references)
    notional_references = top_window_references(report.get("top_by_window"), "total_notional")
    if notional_references:
        updates["strength_notional_reference_by_window"] = tuple(notional_references)

    return replace(config, **updates) if updates else config


def match_trade_to_book(
    *,
    trade_price: float,
    trade_timestamp: float,
    book: BookMetrics,
    book_timestamp: float | None,
    max_lag_seconds: float,
) -> TradeBookMatch:
    if book_timestamp is None:
        return TradeBookMatch(outside=None, gap_bps=None, book_age_ms=None)
    book_age_seconds = trade_timestamp - book_timestamp
    book_age_ms = book_age_seconds * 1000.0
    if (
        book_age_seconds < 0.0
        or book_age_seconds > max_lag_seconds
        or trade_price <= 0.0
        or book.best_bid <= 0.0
        or book.best_ask <= 0.0
        or book.mid <= 0.0
    ):
        return TradeBookMatch(outside=None, gap_bps=None, book_age_ms=book_age_ms)
    if trade_price < book.best_bid:
        return TradeBookMatch(
            outside="below_bid",
            gap_bps=(book.best_bid - trade_price) / book.mid * 10_000.0,
            book_age_ms=book_age_ms,
        )
    if trade_price > book.best_ask:
        return TradeBookMatch(
            outside="above_ask",
            gap_bps=(trade_price - book.best_ask) / book.mid * 10_000.0,
            book_age_ms=book_age_ms,
        )
    return TradeBookMatch(outside=None, gap_bps=0.0, book_age_ms=book_age_ms)


def build_entry_order_plan(
    *,
    stage: Stage,
    direction: Direction | None,
    price: float,
    extreme_price: float,
    strength: float,
    config: PinStageConfig,
) -> EntryOrderPlan | None:
    if stage != Stage.REBOUND_CONFIRMED or direction is None:
        return None
    if price <= 0.0 or extreme_price <= 0.0:
        return None

    allocation_ratio = clamp(strength) ** max(config.entry_order_curve_gamma, 0.0)
    low = config.min_entry_order_notional
    high = config.max_entry_order_notional
    if high < low:
        low, high = high, low
    target_notional = low + allocation_ratio * (high - low)
    order_scale = (
        config.rebound_start_order_scale
        if stage == Stage.REBOUND_START
        else config.rebound_confirmed_order_scale
    )
    total_notional = target_notional * clamp(order_scale)
    if total_notional <= 0.0:
        return None

    entry_side: Literal["long", "short"] = "long" if direction == "down_flush" else "short"
    order_action: Literal["buy", "sell"] = "buy" if direction == "down_flush" else "sell"
    fractions = (1.0,) if stage == Stage.REBOUND_START else entry_slice_fractions(allocation_ratio)
    prices = entry_slice_prices(direction, price, extreme_price, len(fractions))
    slices = tuple(
        {
            "index": index,
            "trigger": trigger,
            "price": slice_price,
            "fraction": fraction,
            "notional": total_notional * fraction,
        }
        for index, (trigger, slice_price, fraction) in enumerate(
            zip(entry_slice_triggers(stage, len(prices)), prices, fractions),
            start=1,
        )
    )
    return EntryOrderPlan(
        entry_side=entry_side,
        order_action=order_action,
        allocation_ratio=allocation_ratio,
        total_notional=total_notional,
        slices=slices,
    )


def entry_slice_fractions(allocation_ratio: float) -> tuple[float, ...]:
    if allocation_ratio < 0.30:
        return (1.0,)
    if allocation_ratio < 0.65:
        return (0.40, 0.60)
    return (0.25, 0.35, 0.40)


def entry_slice_triggers(stage: Stage, count: int) -> tuple[str, ...]:
    if stage == Stage.REBOUND_START:
        return ("rebound_start_probe",)
    if count <= 1:
        return ("rebound_confirmed",)
    if count == 2:
        return ("rebound_confirmed", "half_retest")
    return ("rebound_confirmed", "half_retest", "extreme_retest")


def entry_slice_prices(direction: Direction, price: float, extreme_price: float, count: int) -> tuple[float, ...]:
    if count <= 1:
        return (price,)
    midpoint = (price + extreme_price) / 2.0
    if count == 2:
        return (price, midpoint)
    return (price, midpoint, extreme_price)


class OrderBook:
    def __init__(self) -> None:
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}

    def apply_snapshot(
        self,
        bids: Iterable[tuple[str, str]],
        asks: Iterable[tuple[str, str]],
    ) -> None:
        self._bids.clear()
        self._asks.clear()
        self.apply_delta(bids=bids, asks=asks)

    def apply_delta(
        self,
        bids: Iterable[tuple[str, str]],
        asks: Iterable[tuple[str, str]],
    ) -> None:
        self._apply_side(self._bids, bids)
        self._apply_side(self._asks, asks)

    def metrics(self) -> BookMetrics:
        if not self._bids or not self._asks:
            return empty_book_metrics()

        best_bid = max(self._bids)
        best_ask = min(self._asks)
        mid = (best_bid + best_ask) / 2.0
        bid_levels = sorted(self._bids.items(), reverse=True)
        ask_levels = sorted(self._asks.items())
        bid_depth_5 = sum(quantity for _, quantity in bid_levels[:5])
        ask_depth_5 = sum(quantity for _, quantity in ask_levels[:5])
        bid_depth_25 = sum(quantity for _, quantity in bid_levels[:25])
        ask_depth_25 = sum(quantity for _, quantity in ask_levels[:25])
        bid_depth_10bp = sum(quantity for price, quantity in bid_levels if price >= mid * 0.999)
        ask_depth_10bp = sum(quantity for price, quantity in ask_levels if price <= mid * 1.001)

        return BookMetrics(
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=best_ask - best_bid,
            bid_depth_5=bid_depth_5,
            ask_depth_5=ask_depth_5,
            bid_depth_25=bid_depth_25,
            ask_depth_25=ask_depth_25,
            depth_imbalance_5=imbalance(bid_depth_5, ask_depth_5),
            depth_imbalance_25=imbalance(bid_depth_25, ask_depth_25),
            bid_depth_10bp=bid_depth_10bp,
            ask_depth_10bp=ask_depth_10bp,
        )

    @staticmethod
    def _apply_side(book: dict[float, float], entries: Iterable[tuple[str, str]]) -> None:
        for raw_price, raw_quantity in entries:
            price = float(raw_price)
            quantity = float(raw_quantity)
            if quantity == 0.0:
                book.pop(price, None)
            else:
                book[price] = quantity


class TradeWindow:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._trades: deque[TradeEvent] = deque()
        self._min_prices: deque[tuple[float, float]] = deque()
        self._max_prices: deque[tuple[float, float]] = deque()
        self._buy_notional = 0.0
        self._sell_notional = 0.0

    def add(self, trade: TradeEvent) -> None:
        self._trades.append(trade)
        if trade.side == "Buy":
            self._buy_notional += trade.notional
        else:
            self._sell_notional += trade.notional

        while self._min_prices and self._min_prices[-1][1] >= trade.price:
            self._min_prices.pop()
        self._min_prices.append((trade.timestamp, trade.price))

        while self._max_prices and self._max_prices[-1][1] <= trade.price:
            self._max_prices.pop()
        self._max_prices.append((trade.timestamp, trade.price))

        self._evict(trade.timestamp)

    def metrics(self, now: float) -> TradeWindowMetrics:
        self._evict(now)
        if not self._trades:
            return empty_trade_metrics()

        first = self._trades[0]
        last = self._trades[-1]
        low_price = self._min_prices[0][1] if self._min_prices else last.price
        high_price = self._max_prices[0][1] if self._max_prices else last.price
        total = self._buy_notional + self._sell_notional
        elapsed = max(last.timestamp - first.timestamp, 1e-9)
        return TradeWindowMetrics(
            start_price=first.price,
            end_price=last.price,
            low_price=low_price,
            high_price=high_price,
            price_move_pct=(last.price - first.price) / first.price if first.price > 0 else 0.0,
            range_pct=(high_price - low_price) / first.price if first.price > 0 else 0.0,
            trade_count=len(self._trades),
            buy_notional=self._buy_notional,
            sell_notional=self._sell_notional,
            total_notional=total,
            trade_imbalance=imbalance(self._buy_notional, self._sell_notional),
            trades_per_second=len(self._trades) / elapsed,
        )

    def _evict(self, now: float) -> None:
        cutoff = now - self.seconds
        while self._trades and self._trades[0].timestamp < cutoff:
            old = self._trades.popleft()
            if old.side == "Buy":
                self._buy_notional -= old.notional
            else:
                self._sell_notional -= old.notional
        while self._min_prices and self._min_prices[0][0] < cutoff:
            self._min_prices.popleft()
        while self._max_prices and self._max_prices[0][0] < cutoff:
            self._max_prices.popleft()


class PinStageDetector:
    def __init__(self, config: PinStageConfig | None = None) -> None:
        self.config = config or PinStageConfig()
        self.feature_windows = normalize_windows(
            (*self.config.feature_window_seconds, *self.config.trigger_window_seconds, self.config.window_seconds)
        )
        self.trigger_windows = tuple(
            window
            for window in normalize_windows(self.config.trigger_window_seconds or (self.config.window_seconds,))
            if window in self.feature_windows
        )
        self.windows = {window: TradeWindow(window) for window in self.feature_windows}
        self.window = self.windows.get(self.config.window_seconds) or TradeWindow(self.config.window_seconds)
        self.stage = Stage.NORMAL
        self.active: ActiveShock | None = None

    def on_trade(
        self,
        trade: TradeEvent,
        book: BookMetrics | None = None,
        book_timestamp: float | None = None,
    ) -> StageEvent:
        previous_stage = self.stage
        for window in self.windows.values():
            window.add(trade)
        metrics_by_window = self._metrics_by_window(trade.timestamp)
        primary_metrics = self._primary_metrics(metrics_by_window)
        book_metrics = book or empty_book_metrics()
        candidate_signal = self._select_window_signal(metrics_by_window)
        active_signal: WindowSignal | None = None
        trigger_signal = candidate_signal
        direction = candidate_signal.direction if candidate_signal is not None else None
        output_metrics = candidate_signal.metrics if candidate_signal is not None else primary_metrics
        strength = self._strength(metrics_by_window, book_metrics, direction)
        rebound_ratio = 0.0
        depth_drop = self._depth_drop(book_metrics)
        entry_candidate_price: float | None = None
        started_at: float | None = self.active.started_at if self.active else None
        ended_at: float | None = None

        if self.active is None:
            if direction is None:
                self.stage = Stage.NORMAL
            elif candidate_signal is not None and candidate_signal.is_shock:
                self._start_active_shock(trade, candidate_signal.metrics, strength, book_metrics, direction)
                self.stage = Stage.SHOCK_NOW
                started_at = trade.timestamp
            else:
                self.stage = Stage.PRE_SHOCK
        else:
            started_at = self.active.started_at
            direction = self.active.direction
            active_signal = self._select_window_signal(metrics_by_window, direction=direction)
            trigger_signal = active_signal or candidate_signal
            output_metrics = trigger_signal.metrics if trigger_signal is not None else primary_metrics
            strength = self._strength(metrics_by_window, book_metrics, direction)
            candidate_direction = candidate_signal.direction if candidate_signal is not None else None
            reversal_direction = self._opposite_reversal_direction(trade.price)
            if (
                self.stage != Stage.TREND_BREAK
                and reversal_direction is not None
                and (
                    candidate_direction == reversal_direction
                    or candidate_direction is None
                    or candidate_direction == self.active.direction
                )
            ):
                reversal_signal = self._select_window_signal(metrics_by_window, direction=reversal_direction)
                trigger_signal = reversal_signal or candidate_signal
                output_metrics = trigger_signal.metrics if trigger_signal is not None else primary_metrics
                strength = self._strength(metrics_by_window, book_metrics, reversal_direction)
                self._start_active_shock(
                    trade,
                    output_metrics,
                    strength,
                    book_metrics,
                    reversal_direction,
                    anchor_price=self.active.extreme_price,
                )
                self.stage = Stage.SHOCK_NOW
                direction = reversal_direction
                started_at = trade.timestamp
                rebound_ratio = 0.0
            else:
                self._update_active_extreme(trade, output_metrics, strength, book_metrics)
                rebound_ratio = self._rebound_ratio(trade.price)
                strength = max(strength, self.active.peak_strength)
                depth_drop = self._depth_drop(book_metrics)

                if self.stage == Stage.TREND_BREAK:
                    ended_at = self.active.started_at + self.config.trend_break_seconds
                elif self._is_trend_break(trade.timestamp, rebound_ratio):
                    self.stage = Stage.TREND_BREAK
                    ended_at = trade.timestamp
                elif self.stage == Stage.REBOUND_CONFIRMED:
                    if self._should_reset_after_confirmation(trade.timestamp):
                        self._reset()
                        self.stage = Stage.NORMAL
                        direction = None
                        started_at = None
                    else:
                        entry_candidate_price = trade.price
                elif rebound_ratio >= self.config.rebound_confirm_ratio:
                    self.stage = Stage.REBOUND_CONFIRMED
                    entry_candidate_price = trade.price
                    ended_at = trade.timestamp
                elif rebound_ratio >= self.config.rebound_start_ratio:
                    self.stage = Stage.REBOUND_START
                    entry_candidate_price = trade.price
                elif active_signal is not None and active_signal.is_extreme:
                    self.stage = Stage.EXTREME_FLUSH
                elif self.stage not in {Stage.EXTREME_FLUSH, Stage.REBOUND_START}:
                    self.stage = Stage.SHOCK_NOW

        clamped_strength = clamp(strength)
        active_extreme_price = self.active.extreme_price if self.active is not None else trade.price
        entry_order_plan = build_entry_order_plan(
            stage=self.stage,
            direction=direction,
            price=trade.price,
            extreme_price=active_extreme_price,
            strength=clamped_strength,
            config=self.config,
        )
        quote_match = match_trade_to_book(
            trade_price=trade.price,
            trade_timestamp=trade.timestamp,
            book=book_metrics,
            book_timestamp=book_timestamp,
            max_lag_seconds=self.config.max_book_lag_seconds,
        )

        return StageEvent(
            timestamp=trade.timestamp,
            symbol=trade.symbol,
            stage=self.stage,
            direction=direction,
            price=trade.price,
            size=trade.size,
            side=trade.side,
            started_at=started_at,
            ended_at=ended_at,
            entry_candidate_price=entry_candidate_price,
            strength=clamped_strength,
            trade_notional=output_metrics.total_notional,
            trade_imbalance=output_metrics.trade_imbalance,
            book_imbalance=book_metrics.depth_imbalance_25,
            depth_drop=depth_drop,
            rebound_ratio=clamp(rebound_ratio),
            trade_count=output_metrics.trade_count,
            buy_notional=output_metrics.buy_notional,
            sell_notional=output_metrics.sell_notional,
            best_bid=book_metrics.best_bid if book_metrics.best_bid > 0 else None,
            best_ask=book_metrics.best_ask if book_metrics.best_ask > 0 else None,
            mid_price=book_metrics.mid if book_metrics.mid > 0 else None,
            spread=book_metrics.spread if book_metrics.spread > 0 else None,
            book_timestamp=book_timestamp,
            book_age_ms=quote_match.book_age_ms,
            trade_outside_book=quote_match.outside,
            trade_book_gap_bps=quote_match.gap_bps,
            trigger_window_seconds=trigger_signal.window_seconds if trigger_signal is not None else self.config.window_seconds,
            trade_windows=trade_windows_to_dict(metrics_by_window),
            stage_changed=self.stage != previous_stage,
            entry_order_plan=entry_order_plan,
        )

    def _start_active_shock(
        self,
        trade: TradeEvent,
        metrics: TradeWindowMetrics,
        strength: float,
        book: BookMetrics,
        direction: Direction,
        anchor_price: float | None = None,
    ) -> None:
        self.active = ActiveShock(
            direction=direction,
            started_at=trade.timestamp,
            anchor_price=anchor_price if anchor_price is not None else metrics.start_price,
            shock_price=trade.price,
            extreme_price=trade.price,
            peak_trade_notional=metrics.total_notional,
            peak_strength=strength,
            baseline_depth_25=self._directional_depth(book, direction),
        )

    def _opposite_reversal_direction(self, price: float) -> Direction | None:
        if self.active is None or self.active.extreme_price <= 0:
            return None
        if self._rebound_ratio(price) <= 1.0:
            return None
        if (
            self.active.direction == "up_squeeze"
            and price <= self.active.extreme_price * (1.0 - self.config.shock_move_pct)
        ):
            return "down_flush"
        if (
            self.active.direction == "down_flush"
            and price >= self.active.extreme_price * (1.0 + self.config.shock_move_pct)
        ):
            return "up_squeeze"
        return None

    def _metrics_by_window(self, timestamp: float) -> dict[float, TradeWindowMetrics]:
        return {window: state.metrics(timestamp) for window, state in self.windows.items()}

    def _primary_metrics(self, metrics_by_window: dict[float, TradeWindowMetrics]) -> TradeWindowMetrics:
        return metrics_by_window.get(self.config.window_seconds) or next(iter(metrics_by_window.values()), empty_trade_metrics())

    def _select_window_signal(
        self,
        metrics_by_window: dict[float, TradeWindowMetrics],
        direction: Direction | None = None,
    ) -> WindowSignal | None:
        best: WindowSignal | None = None
        directions: tuple[Direction, ...] = (direction,) if direction is not None else ("down_flush", "up_squeeze")
        for window in self.trigger_windows:
            metrics = metrics_by_window.get(window)
            if metrics is None or metrics.trade_count < self.config.min_window_trades:
                continue
            for candidate_direction in directions:
                signal = self._window_signal(window, metrics, candidate_direction)
                if signal is None:
                    continue
                if best is None or (signal.is_shock, signal.score) > (best.is_shock, best.score):
                    best = signal
        return best

    def _window_signal(
        self,
        window_seconds: float,
        metrics: TradeWindowMetrics,
        direction: Direction,
    ) -> WindowSignal | None:
        directional_move = self._directional_move(metrics, direction)
        pre_threshold = self._pre_shock_threshold(window_seconds)
        shock_threshold = self._shock_threshold(window_seconds)
        extreme_threshold = self._extreme_threshold(window_seconds)
        scaled_notional = self._notional_threshold(window_seconds)
        aligned_imbalance = (
            -metrics.trade_imbalance if direction == "down_flush" else metrics.trade_imbalance
        )
        has_move_evidence = directional_move >= pre_threshold
        has_flow_evidence = metrics.total_notional >= scaled_notional and aligned_imbalance >= self.config.high_imbalance
        if not has_move_evidence and not has_flow_evidence:
            return None
        score = (
            max(directional_move, 0.0) / max(shock_threshold, 1e-9) * 2.0
            + max(directional_move, 0.0) / max(pre_threshold, 1e-9)
            + max(aligned_imbalance, 0.0) * 0.35
            + min(metrics.total_notional / max(scaled_notional, 1e-9), 10.0) * 0.03
        )
        return WindowSignal(
            window_seconds=window_seconds,
            metrics=metrics,
            direction=direction,
            directional_move_pct=directional_move,
            shock_threshold_pct=shock_threshold,
            extreme_threshold_pct=extreme_threshold,
            score=score,
        )

    @staticmethod
    def _directional_move(metrics: TradeWindowMetrics, direction: Direction) -> float:
        if direction == "down_flush":
            return -metrics.price_move_pct
        return metrics.price_move_pct

    def _pre_shock_threshold(self, window_seconds: float) -> float:
        value = lookup_window_value(self.config.pre_shock_move_pct_by_window, window_seconds)
        if value is not None:
            return min(value, self._shock_threshold(window_seconds) * 0.75)
        scaled = self.config.pre_shock_move_pct * math.sqrt(max(window_seconds, self.config.window_seconds) / max(self.config.window_seconds, 1e-9))
        return min(scaled, self._shock_threshold(window_seconds) * 0.75)

    def _shock_threshold(self, window_seconds: float) -> float:
        value = lookup_window_value(self.config.shock_move_pct_by_window, window_seconds)
        if value is None:
            return self.config.shock_move_pct * math.sqrt(max(window_seconds, self.config.window_seconds) / max(self.config.window_seconds, 1e-9))
        scale_factor = self.config.shock_move_pct / DEFAULT_SHOCK_MOVE_PCT if DEFAULT_SHOCK_MOVE_PCT > 0 else 1.0
        return value * scale_factor

    def _extreme_threshold(self, window_seconds: float) -> float:
        value = lookup_window_value(self.config.extreme_move_pct_by_window, window_seconds)
        if value is None:
            return self.config.extreme_move_pct * math.sqrt(max(window_seconds, self.config.window_seconds) / max(self.config.window_seconds, 1e-9))
        scale_factor = self.config.extreme_move_pct / DEFAULT_EXTREME_MOVE_PCT if DEFAULT_EXTREME_MOVE_PCT > 0 else 1.0
        return value * scale_factor

    def _notional_threshold(self, window_seconds: float) -> float:
        value = lookup_window_value(self.config.min_trade_notional_by_window, window_seconds)
        if value is not None:
            return value
        return self.config.min_trade_notional * max(window_seconds / max(self.config.window_seconds, 1e-9), 1.0)

    def _candidate_direction(self, metrics: TradeWindowMetrics) -> Direction | None:
        if metrics.start_price <= 0:
            return None
        if metrics.price_move_pct <= -self.config.pre_shock_move_pct:
            return "down_flush"
        if metrics.price_move_pct >= self.config.pre_shock_move_pct:
            return "up_squeeze"
        if (
            metrics.total_notional >= self.config.min_trade_notional
            and metrics.trade_imbalance <= -self.config.high_imbalance
        ):
            return "down_flush"
        if (
            metrics.total_notional >= self.config.min_trade_notional
            and metrics.trade_imbalance >= self.config.high_imbalance
        ):
            return "up_squeeze"
        return None

    def _is_shock(self, metrics: TradeWindowMetrics, direction: Direction) -> bool:
        if metrics.trade_count < self.config.min_window_trades:
            return False
        if direction == "down_flush":
            return -metrics.price_move_pct >= self._shock_threshold(self.config.window_seconds)
        return metrics.price_move_pct >= self._shock_threshold(self.config.window_seconds)

    def _is_extreme(self, metrics: TradeWindowMetrics, direction: Direction) -> bool:
        if metrics.trade_count < self.config.min_window_trades:
            return False
        if direction == "down_flush":
            return -metrics.price_move_pct >= self._extreme_threshold(self.config.window_seconds)
        return metrics.price_move_pct >= self._extreme_threshold(self.config.window_seconds)

    def _update_active_extreme(
        self,
        trade: TradeEvent,
        metrics: TradeWindowMetrics,
        strength: float,
        book: BookMetrics,
    ) -> None:
        if self.active is None:
            return
        extreme_price = self.active.extreme_price
        if self.active.direction == "down_flush":
            extreme_price = min(extreme_price, trade.price, metrics.low_price)
        else:
            extreme_price = max(extreme_price, trade.price, metrics.high_price)
        self.active.extreme_price = extreme_price
        self.active.peak_trade_notional = max(self.active.peak_trade_notional, metrics.total_notional)
        self.active.peak_strength = max(self.active.peak_strength, strength)
        if self.active.baseline_depth_25 is None:
            self.active.baseline_depth_25 = self._directional_depth(book, self.active.direction)

    def _rebound_ratio(self, price: float) -> float:
        if self.active is None:
            return 0.0
        if self.active.direction == "down_flush":
            drop = self.active.anchor_price - self.active.extreme_price
            return 0.0 if drop <= 0 else (price - self.active.extreme_price) / drop
        rise = self.active.extreme_price - self.active.anchor_price
        return 0.0 if rise <= 0 else (self.active.extreme_price - price) / rise

    def _is_trend_break(self, timestamp: float, rebound_ratio: float) -> bool:
        if self.active is None:
            return False
        return (
            timestamp - self.active.started_at >= self.config.trend_break_seconds
            and rebound_ratio < self.config.rebound_start_ratio
        )

    def _should_reset_after_confirmation(self, timestamp: float) -> bool:
        return self.active is not None and timestamp - self.active.started_at >= self.config.stage_reset_seconds

    def _strength(
        self,
        metrics_by_window: dict[float, TradeWindowMetrics],
        book: BookMetrics,
        direction: Direction | None,
    ) -> float:
        if not metrics_by_window:
            metrics_by_window = {self.config.window_seconds: empty_trade_metrics()}
        move_score = 0.0
        notional_score = 0.0
        imbalance_score = 0.0
        for window, metrics in metrics_by_window.items():
            move_reference = self._strength_move_reference(window)
            notional_reference = self._strength_notional_reference(window)
            if direction is None:
                directional_move = abs(metrics.price_move_pct)
            else:
                directional_move = max(self._directional_move(metrics, direction), 0.0)
            move_score = max(move_score, min(directional_move / move_reference, 1.0))
            notional_score = max(notional_score, min(metrics.total_notional / notional_reference, 1.0))
            imbalance_score = max(imbalance_score, abs(metrics.trade_imbalance))
        book_score = abs(book.depth_imbalance_25)
        aligned_book_score = 0.0
        if direction == "down_flush":
            aligned_book_score = max(-book.depth_imbalance_25, 0.0)
        elif direction == "up_squeeze":
            aligned_book_score = max(book.depth_imbalance_25, 0.0)
        return clamp(
            0.40 * move_score
            + 0.25 * notional_score
            + 0.20 * imbalance_score
            + 0.10 * book_score
            + 0.05 * aligned_book_score
        )

    def _strength_move_reference(self, window_seconds: float) -> float:
        return lookup_window_value(
            self.config.strength_move_reference_by_window_pct,
            window_seconds,
            self.config.strength_move_reference_pct,
        )

    def _strength_notional_reference(self, window_seconds: float) -> float:
        return lookup_window_value(
            self.config.strength_notional_reference_by_window,
            window_seconds,
            self.config.strength_notional_reference,
        )

    def _directional_depth(self, book: BookMetrics, direction: Direction) -> float | None:
        if book.mid <= 0:
            return None
        return book.bid_depth_25 if direction == "down_flush" else book.ask_depth_25

    def _depth_drop(self, book: BookMetrics) -> float:
        if self.active is None or self.active.baseline_depth_25 is None or self.active.baseline_depth_25 <= 0:
            return 0.0
        current = self._directional_depth(book, self.active.direction)
        if current is None:
            return 0.0
        return clamp((self.active.baseline_depth_25 - current) / self.active.baseline_depth_25)

    def _reset(self) -> None:
        self.active = None


def replay_history(
    *,
    trade_path: str | Path,
    orderbook_path: str | Path,
    symbol: str,
    config: PinStageConfig | None = None,
    sample_seconds: float = 1.0,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> list[StageEvent]:
    detector = PinStageDetector(config)
    book = OrderBook()
    last_book_metrics = empty_book_metrics()
    last_book_timestamp: float | None = None
    events: list[StageEvent] = []
    next_sample_at: float | None = None
    last_stage: Stage | None = None

    trades = iter_trade_events(trade_path, symbol=symbol)
    books = iter_orderbook_events(orderbook_path)
    next_trade = next_or_none(trades)
    next_book = next_or_none(books)

    while next_trade is not None or next_book is not None:
        trade_ts = next_trade.timestamp if next_trade is not None else math.inf
        book_ts = next_book.timestamp if next_book is not None else math.inf
        if book_ts <= trade_ts:
            if end_ts is not None and next_book.timestamp > end_ts:
                next_book = None
                continue
            if next_book.event_type == "snapshot":
                book.apply_snapshot(next_book.bids, next_book.asks)
            else:
                book.apply_delta(next_book.bids, next_book.asks)
            last_book_metrics = book.metrics()
            last_book_timestamp = next_book.timestamp
            next_book = next_or_none(books)
            continue

        trade = next_trade
        next_trade = next_or_none(trades)
        if start_ts is not None and trade.timestamp < start_ts:
            continue
        if end_ts is not None and trade.timestamp > end_ts:
            break

        stage_event = detector.on_trade(trade, last_book_metrics, book_timestamp=last_book_timestamp)
        if next_sample_at is None:
            next_sample_at = trade.timestamp
        should_emit = (
            stage_event.stage_changed
            or stage_event.stage != last_stage
            or trade.timestamp >= next_sample_at
        )
        if should_emit:
            events.append(stage_event)
            last_stage = stage_event.stage
            while next_sample_at <= trade.timestamp:
                next_sample_at += sample_seconds

    return events


def iter_trade_events(path: str | Path, symbol: str | None = None) -> Iterator[TradeEvent]:
    with gzip.open(path, "rt", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_symbol = row["symbol"]
            if symbol is not None and row_symbol != symbol:
                continue
            side = row["side"]
            if side not in {"Buy", "Sell"}:
                continue
            yield TradeEvent(
                timestamp=float(row["timestamp"]),
                symbol=row_symbol,
                side=side,  # type: ignore[arg-type]
                size=float(row["size"]),
                price=float(row["price"]),
            )


def iter_orderbook_events(path: str | Path) -> Iterator[OrderBookEvent]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not names:
            return
        with archive.open(names[0]) as handle:
            for raw_line in handle:
                payload = json.loads(raw_line)
                data = payload.get("data", {})
                yield OrderBookEvent(
                    timestamp=float(payload["ts"]) / 1000.0,
                    event_type=payload["type"],
                    bids=[tuple(item) for item in data.get("b", [])],
                    asks=[tuple(item) for item in data.get("a", [])],
                )


def load_candles_csv(path: str | Path, start_ts: float | None = None, end_ts: float | None = None) -> list[CandleBar]:
    candles: list[CandleBar] = []
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            timestamp = parse_timestamp(row["timestamp"])
            if start_ts is not None and timestamp < start_ts:
                continue
            if end_ts is not None and timestamp > end_ts:
                continue
            candles.append(
                CandleBar(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return candles


def derive_candles_from_trades(
    path: str | Path,
    *,
    symbol: str,
    seconds: float,
    start_ts: float | None = None,
    end_ts: float | None = None,
) -> list[CandleBar]:
    buckets: dict[float, dict[str, float]] = {}
    for trade in iter_trade_events(path, symbol=symbol):
        if start_ts is not None and trade.timestamp < start_ts:
            continue
        if end_ts is not None and trade.timestamp > end_ts:
            break
        bucket = math.floor(trade.timestamp / seconds) * seconds
        candle = buckets.get(bucket)
        if candle is None:
            candle = {
                "open": trade.price,
                "high": trade.price,
                "low": trade.price,
                "close": trade.price,
                "volume": 0.0,
            }
            buckets[bucket] = candle
        candle["high"] = max(candle["high"], trade.price)
        candle["low"] = min(candle["low"], trade.price)
        candle["close"] = trade.price
        candle["volume"] += trade.size
    return [
        CandleBar(
            timestamp=timestamp,
            open=candle["open"],
            high=candle["high"],
            low=candle["low"],
            close=candle["close"],
            volume=candle["volume"],
        )
        for timestamp, candle in sorted(buckets.items())
    ]


def annotate_events_with_wick_features(
    events: list[StageEvent],
    candles: list[CandleBar],
    config: PinStageConfig | None = None,
) -> list[StageEvent]:
    if not events or not candles:
        return events
    cfg = config or PinStageConfig()
    sorted_candles = sorted(candles, key=lambda item: item.timestamp)
    candle_context = slow_wick_candle_context(sorted_candles, cfg)
    annotated: list[StageEvent] = []
    candle_index = 0
    for event in events:
        while (
            candle_index + 1 < len(sorted_candles)
            and sorted_candles[candle_index + 1].timestamp <= event.timestamp
        ):
            candle_index += 1
        candle = sorted_candles[candle_index]
        if not (candle.timestamp <= event.timestamp < candle.timestamp + cfg.slow_wick_window_seconds):
            annotated.append(event)
            continue
        features = slow_wick_features(candle, cfg, candle_context.get(candle.timestamp, {}))
        annotated.append(replace(event, **features) if features else event)
    return annotated


def slow_wick_candle_context(
    candles: list[CandleBar],
    config: PinStageConfig,
) -> dict[float, dict[str, float | bool | None]]:
    lookback = max(int(config.slow_wick_trend_lookback_candles), 0)
    ema_period = max(int(config.slow_wick_ema_period), 1)
    vwap_lookback = max(int(config.slow_wick_vwap_lookback_candles), 1)
    alpha = 2.0 / (ema_period + 1.0)
    ema: float | None = None
    context: dict[float, dict[str, float | bool | None]] = {}
    for index, candle in enumerate(candles):
        if ema is None:
            ema = candle.close
        else:
            ema = candle.close * alpha + ema * (1.0 - alpha)
        previous = candles[max(0, index - lookback) : index] if lookback > 0 else []
        prev_low = min((item.low for item in previous), default=None)
        breaks_prev_low = prev_low is not None and candle.low < prev_low
        close_above_ema = candle.close >= ema
        vwap_candles = candles[max(0, index - vwap_lookback + 1) : index + 1]
        vwap_volume = sum(max(item.volume, 0.0) for item in vwap_candles)
        vwap = (
            sum(candle_typical_price(item) * max(item.volume, 0.0) for item in vwap_candles) / vwap_volume
            if vwap_volume > 0.0
            else None
        )
        close_above_vwap = vwap is not None and candle.close >= vwap
        context[candle.timestamp] = {
            "prev_low": prev_low,
            "breaks_prev_low": breaks_prev_low,
            "ema": ema,
            "close_above_ema": close_above_ema,
            "vwap": vwap,
            "close_above_vwap": close_above_vwap,
            "trend_filter_passed": (not breaks_prev_low) or close_above_ema or close_above_vwap,
        }
    return context


def candle_typical_price(candle: CandleBar) -> float:
    return (candle.high + candle.low + candle.close) / 3.0


def slow_wick_features(
    candle: CandleBar,
    config: PinStageConfig,
    context: dict[str, float | bool | None] | None = None,
) -> dict[str, object]:
    price_range = candle.high - candle.low
    if candle.open <= 0.0 or price_range <= 0.0:
        return {}
    body_top = max(candle.open, candle.close)
    body_bottom = min(candle.open, candle.close)
    upper_ratio = (candle.high - body_top) / price_range
    lower_ratio = (body_bottom - candle.low) / price_range
    range_pct = price_range / candle.open
    direction: Direction | None = None
    slow_stage: Literal["15m_lower_wick_candidate", "15m_upper_wick_candidate", "slow_rebound_confirmed"] | None = None
    rebound_ratio = 0.0
    if lower_ratio >= config.slow_wick_min_ratio and range_pct >= config.slow_wick_min_range_pct:
        direction = "down_flush"
        slow_stage = "15m_lower_wick_candidate"
        rebound_ratio = (candle.close - candle.low) / price_range
    elif upper_ratio >= config.slow_wick_min_ratio and range_pct >= config.slow_wick_min_range_pct:
        direction = "up_squeeze"
        slow_stage = "15m_upper_wick_candidate"
        rebound_ratio = (candle.high - candle.close) / price_range
    if direction is None or slow_stage is None:
        return {}
    rebound_confirmed = rebound_ratio >= config.slow_wick_rebound_ratio
    if rebound_confirmed:
        slow_stage = "slow_rebound_confirmed"
    context = context or {}
    return {
        "slow_wick_stage": slow_stage,
        "slow_wick_candidate": True,
        "slow_wick_rebound_confirmed": rebound_confirmed,
        "slow_wick_direction": direction,
        "slow_wick_timestamp": candle.timestamp,
        "slow_wick_window_seconds": config.slow_wick_window_seconds,
        "slow_wick_open": candle.open,
        "slow_wick_high": candle.high,
        "slow_wick_low": candle.low,
        "slow_wick_close": candle.close,
        "slow_wick_range_pct": range_pct,
        "slow_wick_lower_ratio": lower_ratio,
        "slow_wick_upper_ratio": upper_ratio,
        "slow_wick_rebound_ratio": rebound_ratio,
        "slow_wick_prev_low": context.get("prev_low"),
        "slow_wick_breaks_prev_low": context.get("breaks_prev_low"),
        "slow_wick_ema": context.get("ema"),
        "slow_wick_close_above_ema": context.get("close_above_ema"),
        "slow_wick_vwap": context.get("vwap"),
        "slow_wick_close_above_vwap": context.get("close_above_vwap"),
        "slow_wick_trend_filter_passed": context.get("trend_filter_passed"),
    }


def export_labels_jsonl(events: Iterable[StageEvent], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_label(), ensure_ascii=True, sort_keys=True))
            handle.write("\n")


def render_html(
    events: list[StageEvent],
    *,
    symbol: str,
    title: str,
    candles: list[CandleBar] | None = None,
) -> str:
    if not events:
        return empty_html(title)

    candles = candles or []
    width = 1220
    height = 720
    x0 = 70
    y0 = 80
    chart_w = 1080
    chart_h = 430
    vol_y = 545
    vol_h = 90
    min_ts = min([event.timestamp for event in events] + [candle.timestamp for candle in candles])
    max_ts = max([event.timestamp for event in events] + [candle.timestamp for candle in candles])
    prices = [event.price for event in events]
    for candle in candles:
        prices.extend([candle.high, candle.low])
    min_price = min(prices)
    max_price = max(prices)
    padding = max((max_price - min_price) * 0.08, 1.0)
    min_price -= padding
    max_price += padding

    candle_markup = render_candles(candles, min_ts, max_ts, min_price, max_price, x0, y0, chart_w, chart_h)
    price_path = render_price_path(events, min_ts, max_ts, min_price, max_price, x0, y0, chart_w, chart_h)
    stage_bands = render_stage_bands(events, min_ts, max_ts, x0, y0, chart_w, chart_h)
    markers = render_markers(events, min_ts, max_ts, min_price, max_price, x0, y0, chart_w, chart_h)
    volume_bars = render_feature_bars(events, min_ts, max_ts, x0, vol_y, chart_w, vol_h)
    stage_counts = {}
    for event in events:
        stage_counts[event.stage.value] = stage_counts.get(event.stage.value, 0) + 1
    summary = ", ".join(f"{stage}={count}" for stage, count in sorted(stage_counts.items()))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{escape(title)}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f8fafc; color: #111827; }}
    main {{ width: {width}px; margin: 0 auto; padding: 24px 0 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 24px; }}
    .meta {{ color: #64748b; font-size: 13px; margin-bottom: 14px; }}
    svg {{ background: #fff; border: 1px solid #d1d5db; }}
    .legend {{ display: flex; gap: 18px; margin-top: 10px; color: #334155; font-size: 13px; }}
    .swatch {{ width: 11px; height: 11px; display: inline-block; margin-right: 6px; vertical-align: -1px; }}
  </style>
</head>
<body>
<main>
  <h1>{escape(title)}</h1>
  <div class="meta">{escape(symbol)}; {len(events)} sampled/stage events; {escape(summary)}</div>
  <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
    <rect x="{x0}" y="{y0}" width="{chart_w}" height="{chart_h}" fill="#ffffff" stroke="#d1d5db"/>
    <text x="18" y="{y0 + 20}" font-size="12" font-weight="700" fill="#475569">Price</text>
    <text x="{x0 + chart_w + 12}" y="{y0 + 8}" font-size="12" fill="#64748b">{max_price:.2f}</text>
    <text x="{x0 + chart_w + 12}" y="{y0 + chart_h}" font-size="12" fill="#64748b">{min_price:.2f}</text>
    <line x1="{x0}" x2="{x0 + chart_w}" y1="{y0 + chart_h / 2:.1f}" y2="{y0 + chart_h / 2:.1f}" stroke="#e5e7eb"/>
    {stage_bands}
    {candle_markup}
    {price_path}
    {markers}
    <rect x="{x0}" y="{vol_y}" width="{chart_w}" height="{vol_h}" fill="#ffffff" stroke="#d1d5db"/>
    <text x="18" y="{vol_y + 20}" font-size="12" font-weight="700" fill="#475569">Notional</text>
    {volume_bars}
    <text x="{x0}" y="{height - 38}" font-size="12" fill="#64748b">{escape(isoformat_seconds(min_ts))}</text>
    <text x="{x0 + chart_w - 180}" y="{height - 38}" font-size="12" fill="#64748b">{escape(isoformat_seconds(max_ts))}</text>
  </svg>
  <div class="legend">
    <span><i class="swatch" style="background:#ef4444"></i>shock/extreme</span>
    <span><i class="swatch" style="background:#0ea5e9"></i>rebound</span>
    <span><i class="swatch" style="background:#111827"></i>trade price path</span>
    <span><i class="swatch" style="background:#94a3b8"></i>auxiliary kline background</span>
    <span><i class="swatch" style="background:#22c55e"></i>15m slow wick candidate</span>
  </div>
</main>
</body>
</html>
"""


def render_candles(
    candles: list[CandleBar],
    min_ts: float,
    max_ts: float,
    min_price: float,
    max_price: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    if not candles:
        return f'<text x="{x0 + 16}" y="{y0 + 24}" font-size="12" fill="#94a3b8">Auxiliary kline not supplied</text>'
    bar_width = max(width / max(len(candles), 1) * 0.55, 1.0)
    parts = []
    for candle in candles:
        x = scale(candle.timestamp, min_ts, max_ts, x0, x0 + width)
        high_y = scale(candle.high, min_price, max_price, y0 + height, y0)
        low_y = scale(candle.low, min_price, max_price, y0 + height, y0)
        open_y = scale(candle.open, min_price, max_price, y0 + height, y0)
        close_y = scale(candle.close, min_price, max_price, y0 + height, y0)
        top = min(open_y, close_y)
        body_h = max(abs(close_y - open_y), 1.0)
        color = "#16a34a" if candle.close >= candle.open else "#dc2626"
        label, label_y, label_color = candle_wick_label(candle, high_y, low_y)
        label_markup = ""
        if label:
            label_markup = (
                f'<text x="{x + bar_width / 2 + 3:.1f}" y="{label_y:.1f}" font-size="10" '
                f'fill="{label_color}" font-weight="700">{escape(label)}</text>'
            )
        parts.append(
            f'<g opacity="0.42"><line x1="{x:.1f}" y1="{high_y:.1f}" x2="{x:.1f}" y2="{low_y:.1f}" '
            f'stroke="{color}" stroke-width="1"/><rect x="{x - bar_width / 2:.1f}" y="{top:.1f}" '
            f'width="{bar_width:.2f}" height="{body_h:.1f}" fill="{color}">{candle_wick_title(candle)}</rect>'
            f'{label_markup}</g>'
        )
    return "".join(parts)


def candle_wick_label(candle: CandleBar, high_y: float, low_y: float) -> tuple[str | None, float, str]:
    price_range = candle.high - candle.low
    if price_range <= 0.0:
        return (None, high_y, "#64748b")
    body_top = max(candle.open, candle.close)
    body_bottom = min(candle.open, candle.close)
    upper_wick_ratio = (candle.high - body_top) / price_range
    lower_wick_ratio = (body_bottom - candle.low) / price_range
    if upper_wick_ratio >= 0.55 and upper_wick_ratio >= lower_wick_ratio:
        return ("上冲影线", max(high_y - 5.0, 12.0), "#dc2626")
    if lower_wick_ratio >= 0.55:
        return ("下冲影线", low_y + 13.0, "#16a34a")
    return (None, high_y, "#64748b")


def candle_wick_title(candle: CandleBar) -> str:
    price_range = candle.high - candle.low
    if price_range <= 0.0:
        return ""
    body_top = max(candle.open, candle.close)
    body_bottom = min(candle.open, candle.close)
    upper_wick_ratio = (candle.high - body_top) / price_range
    lower_wick_ratio = (body_bottom - candle.low) / price_range
    return (
        f"<title>upper_wick={upper_wick_ratio:.3f} "
        f"lower_wick={lower_wick_ratio:.3f}</title>"
    )


def render_price_path(
    events: list[StageEvent],
    min_ts: float,
    max_ts: float,
    min_price: float,
    max_price: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    points = [
        f"{scale(event.timestamp, min_ts, max_ts, x0, x0 + width):.1f},"
        f"{scale(event.price, min_price, max_price, y0 + height, y0):.1f}"
        for event in events
    ]
    return f'<polyline points="{" ".join(points)}" fill="none" stroke="#111827" stroke-width="1.4" opacity="0.75"/>'


def render_stage_bands(
    events: list[StageEvent],
    min_ts: float,
    max_ts: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    parts = []
    for left, right in zip(events, events[1:]):
        if left.stage in {Stage.NORMAL, Stage.PRE_SHOCK}:
            continue
        x = scale(left.timestamp, min_ts, max_ts, x0, x0 + width)
        x2 = scale(right.timestamp, min_ts, max_ts, x0, x0 + width)
        color = stage_color(left.stage)
        parts.append(
            f'<rect x="{x:.1f}" y="{y0}" width="{max(x2 - x, 1.0):.1f}" height="{height}" '
            f'fill="{color}" opacity="0.11"><title>{escape(left.stage.value)} {escape(isoformat_seconds(left.timestamp))}</title></rect>'
        )
    return "".join(parts)


def render_markers(
    events: list[StageEvent],
    min_ts: float,
    max_ts: float,
    min_price: float,
    max_price: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    parts = []
    for event in events:
        if event.stage in {Stage.NORMAL, Stage.PRE_SHOCK} and not event.stage_changed and not event.slow_wick_candidate:
            continue
        x = scale(event.timestamp, min_ts, max_ts, x0, x0 + width)
        y = scale(event.price, min_price, max_price, y0 + height, y0)
        color = slow_wick_color(event) if event.slow_wick_candidate else stage_color(event.stage)
        order_note = ""
        if event.entry_order_plan is not None:
            order_note = (
                f" order={event.entry_order_plan.order_action} "
                f"entry_notional={event.entry_order_plan.total_notional:.2f} "
                f"allocation={event.entry_order_plan.allocation_ratio:.3f}"
            )
        slow_note = ""
        marker_label = event.stage.value
        if event.slow_wick_candidate:
            marker_label = event.slow_wick_stage or "slow_wick_candidate"
            slow_note = (
                f" slow_wick_stage={event.slow_wick_stage} "
                f"slow_wick_direction={event.slow_wick_direction} "
                f"slow_wick_range={event.slow_wick_range_pct:.4f} "
                f"lower={event.slow_wick_lower_ratio:.3f} "
                f"upper={event.slow_wick_upper_ratio:.3f} "
                f"rebound={event.slow_wick_rebound_ratio:.3f}"
            )
        title = (
            f"{event.stage.value} {isoformat_seconds(event.timestamp)} "
            f"price={event.price:.2f} qty={event.size:.6f} "
            f"notional={event.trade_notional:.2f} strength={event.strength:.3f} "
            f"direction={event.direction}{order_note}{slow_note}"
        )
        parts.append(
            f'<g><title>{escape(title)}</title><circle cx="{x:.1f}" cy="{y:.1f}" r="4.2" '
            f'fill="{color}" stroke="#ffffff" stroke-width="1.4"/>'
            f'<text x="{x + 6:.1f}" y="{y - 6:.1f}" font-size="10" fill="{color}" font-weight="700">'
            f'{escape(marker_label)}</text></g>'
        )
    return "".join(parts)


def render_feature_bars(
    events: list[StageEvent],
    min_ts: float,
    max_ts: float,
    x0: float,
    y0: float,
    width: float,
    height: float,
) -> str:
    max_notional = max((event.trade_notional for event in events), default=1.0)
    bar_width = max(width / max(len(events), 1) * 0.8, 0.6)
    parts = []
    for event in events:
        x = scale(event.timestamp, min_ts, max_ts, x0, x0 + width)
        bar_h = scale(event.trade_notional, 0.0, max_notional, 0.0, height)
        color = "#16a34a" if event.trade_imbalance > 0 else "#dc2626"
        parts.append(
            f'<rect x="{x - bar_width / 2:.1f}" y="{y0 + height - bar_h:.1f}" width="{bar_width:.2f}" '
            f'height="{bar_h:.1f}" fill="{color}" opacity="0.38"/>'
        )
    return "".join(parts)


def stage_color(stage: Stage) -> str:
    return {
        Stage.NORMAL: "#64748b",
        Stage.PRE_SHOCK: "#f59e0b",
        Stage.SHOCK_NOW: "#ef4444",
        Stage.EXTREME_FLUSH: "#991b1b",
        Stage.REBOUND_START: "#0ea5e9",
        Stage.REBOUND_CONFIRMED: "#10b981",
        Stage.TREND_BREAK: "#7c3aed",
    }[stage]


def slow_wick_color(event: StageEvent) -> str:
    if event.slow_wick_stage == "slow_rebound_confirmed":
        return "#22c55e" if event.slow_wick_direction == "down_flush" else "#0ea5e9"
    return "#84cc16" if event.slow_wick_direction == "down_flush" else "#38bdf8"


def empty_book_metrics() -> BookMetrics:
    return BookMetrics(
        best_bid=0.0,
        best_ask=0.0,
        mid=0.0,
        spread=0.0,
        bid_depth_5=0.0,
        ask_depth_5=0.0,
        bid_depth_25=0.0,
        ask_depth_25=0.0,
        depth_imbalance_5=0.0,
        depth_imbalance_25=0.0,
        bid_depth_10bp=0.0,
        ask_depth_10bp=0.0,
    )


def empty_trade_metrics() -> TradeWindowMetrics:
    return TradeWindowMetrics(
        start_price=0.0,
        end_price=0.0,
        low_price=0.0,
        high_price=0.0,
        price_move_pct=0.0,
        range_pct=0.0,
        trade_count=0,
        buy_notional=0.0,
        sell_notional=0.0,
        total_notional=0.0,
        trade_imbalance=0.0,
        trades_per_second=0.0,
    )


def normalize_windows(windows: Iterable[float]) -> tuple[float, ...]:
    normalized = sorted({float(window) for window in windows if float(window) > 0.0})
    return tuple(normalized)


def lookup_window_value(
    pairs: Iterable[tuple[float, float]],
    window_seconds: float,
    fallback: float | None = None,
) -> float | None:
    for raw_window, raw_value in pairs:
        if abs(float(raw_window) - window_seconds) < 1e-9 and raw_value > 0.0:
            return float(raw_value)
    return float(fallback) if fallback is not None and fallback > 0.0 else None


def trade_windows_to_dict(metrics_by_window: dict[float, TradeWindowMetrics]) -> dict[str, dict[str, float]]:
    return {
        format_window_label(window) or f"{window:g}s": trade_window_metrics_to_dict(metrics)
        for window, metrics in sorted(metrics_by_window.items())
    }


def trade_window_metrics_to_dict(metrics: TradeWindowMetrics) -> dict[str, float]:
    return {
        "start_price": metrics.start_price,
        "end_price": metrics.end_price,
        "low_price": metrics.low_price,
        "high_price": metrics.high_price,
        "price_move_pct": metrics.price_move_pct,
        "range_pct": metrics.range_pct,
        "trade_count": float(metrics.trade_count),
        "buy_notional": metrics.buy_notional,
        "sell_notional": metrics.sell_notional,
        "total_notional": metrics.total_notional,
        "trade_imbalance": metrics.trade_imbalance,
        "trades_per_second": metrics.trades_per_second,
    }


def imbalance(left: float, right: float) -> float:
    total = left + right
    if total <= 0:
        return 0.0
    return (left - right) / total


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0.0 and math.isfinite(number) else None


def format_window_label(value: object) -> str | None:
    seconds = positive_float(value)
    if seconds is None:
        return None
    if seconds.is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def parse_window_label(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return positive_float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    if raw.endswith("ms"):
        number = positive_float(raw[:-2])
        return number / 1000.0 if number is not None else None
    if raw.endswith("s"):
        return positive_float(raw[:-1])
    if raw.endswith("m"):
        number = positive_float(raw[:-1])
        return number * 60.0 if number is not None else None
    return positive_float(raw)


def top_window_references(top_by_window: object, field: str) -> list[tuple[float, float]]:
    if not isinstance(top_by_window, dict):
        return []
    references: list[tuple[float, float]] = []
    for window_label, rows in top_by_window.items():
        window_seconds = parse_window_label(window_label)
        if window_seconds is None or not isinstance(rows, list):
            continue
        values = [
            number
            for row in rows
            if isinstance(row, dict)
            for number in [positive_float(row.get(field))]
            if number is not None
        ]
        if values:
            references.append((window_seconds, max(values)))
    return sorted(references)


def max_top_notional(top_by_window: object, primary_window: str | None) -> float | None:
    if not isinstance(top_by_window, dict) or primary_window is None:
        return None
    rows = top_by_window.get(primary_window)
    if rows is None and primary_window == "60s":
        rows = top_by_window.get("1m")
    if rows is None and primary_window == "1m":
        rows = top_by_window.get("60s")
    if not isinstance(rows, list):
        return None
    values = [
        notional
        for row in rows
        if isinstance(row, dict)
        for notional in [positive_float(row.get("total_notional"))]
        if notional is not None
    ]
    return max(values) if values else None


def scale(value: float, source_min: float, source_max: float, target_min: float, target_max: float) -> float:
    if source_max == source_min:
        return (target_min + target_max) / 2.0
    return target_min + (value - source_min) / (source_max - source_min) * (target_max - target_min)


def isoformat_seconds(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def parse_timestamp(value: str) -> float:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()


def parse_windows_arg(value: str) -> tuple[float, ...]:
    return normalize_windows(float(item.strip()) for item in value.split(",") if item.strip())


def next_or_none(iterator: Iterator[object]):
    try:
        return next(iterator)
    except StopIteration:
        return None


def empty_html(title: str) -> str:
    return f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{escape(title)}</title></head><body>No events.</body></html>"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay Bybit BTCUSDT trade/orderbook history and label pin stages.")
    parser.add_argument("--trade", required=True, help="Bybit publicTrade CSV.gz path.")
    parser.add_argument("--orderbook", required=True, help="Bybit orderbook.200 data.zip path.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--output-html", default="btcusdt_pin_stages.html")
    parser.add_argument("--output-labels", default="btcusdt_pin_labels.jsonl")
    parser.add_argument("--kline-csv", help="Optional auxiliary kline CSV with timestamp,open,high,low,close,volume.")
    parser.add_argument(
        "--derive-kline-from-trades-seconds",
        type=float,
        default=0.0,
        help="Diagnostic fallback: derive auxiliary candles from publicTrade at this interval, e.g. 900 for 15m.",
    )
    parser.add_argument("--calibration-json", help="Optional extreme standard JSON from pin_extreme_calibration.py.")
    parser.add_argument("--start", help="Optional UTC ISO start time.")
    parser.add_argument("--end", help="Optional UTC ISO end time.")
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument(
        "--feature-windows",
        default="1,5,15,30,45,60,120,180,240",
        help="Comma-separated trade-flow windows exported as features.",
    )
    parser.add_argument(
        "--trigger-windows",
        default="5,15,30,45,60,120,180,240",
        help="Comma-separated trade-flow windows allowed to trigger pin stages.",
    )
    parser.add_argument("--min-window-trades", type=int, default=3)
    parser.add_argument("--shock-move-pct", type=float, default=0.006)
    parser.add_argument("--extreme-move-pct", type=float, default=0.009)
    parser.add_argument("--rebound-start-ratio", type=float, default=0.25)
    parser.add_argument("--rebound-confirm-ratio", type=float, default=0.55)
    parser.add_argument("--trend-break-seconds", type=float, default=90.0)
    parser.add_argument("--min-entry-order-notional", type=float, default=1_000.0)
    parser.add_argument("--max-entry-order-notional", type=float, default=10_000.0)
    parser.add_argument("--entry-order-curve-gamma", type=float, default=1.5)
    parser.add_argument("--rebound-start-order-scale", type=float, default=0.0)
    parser.add_argument("--rebound-confirmed-order-scale", type=float, default=1.0)
    parser.add_argument("--slow-wick-trend-lookback-candles", type=int, default=4)
    parser.add_argument("--slow-wick-ema-period", type=int, default=9)
    parser.add_argument("--slow-wick-vwap-lookback-candles", type=int, default=4)
    parser.add_argument(
        "--max-book-lag-ms",
        type=float,
        default=500.0,
        help="Only compare trade price with bid1/ask1 when the latest orderbook is this fresh.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    start_ts = parse_timestamp(args.start) if args.start else None
    end_ts = parse_timestamp(args.end) if args.end else None
    config = PinStageConfig(
        window_seconds=args.window_seconds,
        feature_window_seconds=parse_windows_arg(args.feature_windows),
        trigger_window_seconds=parse_windows_arg(args.trigger_windows),
        min_window_trades=args.min_window_trades,
        shock_move_pct=args.shock_move_pct,
        extreme_move_pct=args.extreme_move_pct,
        rebound_start_ratio=args.rebound_start_ratio,
        rebound_confirm_ratio=args.rebound_confirm_ratio,
        trend_break_seconds=args.trend_break_seconds,
        min_entry_order_notional=args.min_entry_order_notional,
        max_entry_order_notional=args.max_entry_order_notional,
        entry_order_curve_gamma=args.entry_order_curve_gamma,
        rebound_start_order_scale=args.rebound_start_order_scale,
        rebound_confirmed_order_scale=args.rebound_confirmed_order_scale,
        max_book_lag_seconds=args.max_book_lag_ms / 1000.0,
        slow_wick_trend_lookback_candles=args.slow_wick_trend_lookback_candles,
        slow_wick_ema_period=args.slow_wick_ema_period,
        slow_wick_vwap_lookback_candles=args.slow_wick_vwap_lookback_candles,
    )
    if args.calibration_json:
        config = apply_extreme_standard(config, args.calibration_json)
    events = replay_history(
        trade_path=args.trade,
        orderbook_path=args.orderbook,
        symbol=args.symbol,
        config=config,
        sample_seconds=args.sample_seconds,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    candles: list[CandleBar] = []
    if args.kline_csv:
        candles = load_candles_csv(args.kline_csv, start_ts=start_ts, end_ts=end_ts)
    elif args.derive_kline_from_trades_seconds > 0.0:
        candles = derive_candles_from_trades(
            args.trade,
            symbol=args.symbol,
            seconds=args.derive_kline_from_trades_seconds,
            start_ts=start_ts,
            end_ts=end_ts,
        )
    events = annotate_events_with_wick_features(events, candles, config)
    export_labels_jsonl(events, args.output_labels)
    html = render_html(events, symbol=args.symbol, title=f"{args.symbol} pin-stage replay", candles=candles)
    Path(args.output_html).write_text(html, encoding="utf-8")
    print(f"events: {len(events)}")
    print(f"labels: {args.output_labels}")
    print(f"html: {args.output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
