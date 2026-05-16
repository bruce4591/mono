from __future__ import annotations

import argparse
import bisect
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from market.trade.pin_stage_replay import (
    PinStageConfig,
    Stage,
    annotate_events_with_wick_features,
    apply_extreme_standard,
    build_entry_order_plan,
    derive_candles_from_trades,
    isoformat_seconds,
    parse_windows_arg,
    parse_timestamp,
    replay_history,
)
from market.trade.pin_kline_labels import KlineLabelConfig, label_candles, load_binance_klines_csv
from market.trade.pin_threshold_learning import apply_learned_thresholds, score_learned_candidate_signal


Strategy = Literal["fixed", "adaptive"]
VALID_KLINE_CANDIDATE_DIRECTIONS = ("down_flush", "up_squeeze")
DEFAULT_KLINE_CANDIDATE_DIRECTIONS = ("down_flush",)


@dataclass(frozen=True)
class LabelBacktestConfig:
    initial_cash: float = 100_000.0
    fixed_order_notional: float = 3_000.0
    max_entry_order_notional: float = 10_000.0
    take_profit_pct: float = 0.006
    fee_rate: float = 0.0006
    max_open_positions: int = 30
    pending_slice_ttl_seconds: float = 180.0
    slow_wick_min_book_imbalance: float = 0.0
    slow_wick_min_trade_imbalance: float = -0.10
    slow_wick_require_price_at_or_above_mid: bool = True
    slow_wick_require_trend_filter: bool = True


@dataclass(frozen=True)
class LabelPosition:
    entry_time: str
    entry_ts: float
    entry_price: float
    quantity: float
    notional: float
    fee: float
    signal_id: str


@dataclass(frozen=True)
class PendingSlice:
    created_ts: float
    signal_id: str
    price: float
    notional: float


@dataclass(frozen=True)
class LabelTrade:
    timestamp: str
    side: Literal["buy", "sell"]
    price: float
    quantity: float
    notional: float
    fee: float
    realized_pnl: float
    reason: str
    signal_id: str


@dataclass
class LabelBacktestResult:
    strategy: Strategy
    config: LabelBacktestConfig
    cash: float
    last_price: float = 0.0
    trades: list[LabelTrade] = field(default_factory=list)
    open_positions: list[LabelPosition] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return sum(1 for trade in self.trades if trade.side == "buy")

    @property
    def exit_count(self) -> int:
        return sum(1 for trade in self.trades if trade.side == "sell")

    @property
    def total_entry_notional(self) -> float:
        return sum(trade.notional for trade in self.trades if trade.side == "buy")

    @property
    def realized_pnl(self) -> float:
        return sum(trade.realized_pnl for trade in self.trades)

    @property
    def closed_trades(self) -> list[LabelTrade]:
        return [trade for trade in self.trades if trade.side == "sell"]

    @property
    def winning_trades(self) -> list[LabelTrade]:
        return [trade for trade in self.closed_trades if trade.realized_pnl > 0.0]

    @property
    def losing_trades(self) -> list[LabelTrade]:
        return [trade for trade in self.closed_trades if trade.realized_pnl < 0.0]

    @property
    def gross_profit(self) -> float:
        return sum(trade.realized_pnl for trade in self.winning_trades)

    @property
    def gross_loss(self) -> float:
        return abs(sum(trade.realized_pnl for trade in self.losing_trades))

    @property
    def win_rate(self) -> float:
        closed_count = len(self.closed_trades)
        return len(self.winning_trades) / closed_count if closed_count else 0.0

    @property
    def average_win(self) -> float:
        return self.gross_profit / len(self.winning_trades) if self.winning_trades else 0.0

    @property
    def average_loss(self) -> float:
        return self.gross_loss / len(self.losing_trades) if self.losing_trades else 0.0

    @property
    def win_loss_ratio(self) -> float | None:
        return self.average_win / self.average_loss if self.average_loss > 0.0 else None

    @property
    def profit_factor(self) -> float | None:
        return self.gross_profit / self.gross_loss if self.gross_loss > 0.0 else None

    @property
    def open_market_value(self) -> float:
        return sum(position.quantity * self.last_price for position in self.open_positions)

    @property
    def final_equity(self) -> float:
        return self.cash + self.open_market_value

    @property
    def return_pct(self) -> float:
        if self.config.initial_cash <= 0.0:
            return 0.0
        return (self.final_equity / self.config.initial_cash - 1.0) * 100.0

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "initial_cash": self.config.initial_cash,
            "final_equity": self.final_equity,
            "return_pct": self.return_pct,
            "realized_pnl": self.realized_pnl,
            "cash": self.cash,
            "open_market_value": self.open_market_value,
            "entry_count": self.entry_count,
            "exit_count": self.exit_count,
            "closed_trade_count": len(self.closed_trades),
            "winning_trade_count": len(self.winning_trades),
            "losing_trade_count": len(self.losing_trades),
            "win_rate": self.win_rate,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "average_win": self.average_win,
            "average_loss": self.average_loss,
            "win_loss_ratio": self.win_loss_ratio,
            "profit_factor": self.profit_factor,
            "open_positions": len(self.open_positions),
            "total_entry_notional": self.total_entry_notional,
        }


@dataclass(frozen=True)
class LabelBacktestComparison:
    fixed: LabelBacktestResult
    adaptive: LabelBacktestResult

    def to_dict(self) -> dict[str, object]:
        fixed = self.fixed.to_dict()
        adaptive = self.adaptive.to_dict()
        return {
            "fixed": fixed,
            "adaptive": adaptive,
            "delta": {
                "final_equity": adaptive["final_equity"] - fixed["final_equity"],  # type: ignore[operator]
                "realized_pnl": adaptive["realized_pnl"] - fixed["realized_pnl"],  # type: ignore[operator]
                "total_entry_notional": adaptive["total_entry_notional"] - fixed["total_entry_notional"],  # type: ignore[operator]
                "entry_count": adaptive["entry_count"] - fixed["entry_count"],  # type: ignore[operator]
            },
        }


@dataclass(frozen=True)
class BatchReplayResult:
    label_count: int
    replayed_dates: tuple[str, ...]
    skipped_dates: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "label_count": self.label_count,
            "replayed_dates": list(self.replayed_dates),
            "skipped_dates": list(self.skipped_dates),
        }


@dataclass(frozen=True)
class CandidateSignalScorer:
    down_wick: dict[str, object] | None = None
    up_wick: dict[str, object] | None = None
    trend_break: dict[str, object] | None = None
    min_candidate_score: float = 1.0
    min_trend_score: float = 1.0


@dataclass(frozen=True)
class KlineCurveCandidateConfig:
    windows_minutes: tuple[int, ...] = (1, 15)
    directions: tuple[str, ...] = DEFAULT_KLINE_CANDIDATE_DIRECTIONS
    min_curve_score: float = 0.80
    lookahead_seconds: float = 180.0
    history_size: int = 240
    cluster_seconds: float = 900.0
    min_cluster_score: float = 0.50


@dataclass(frozen=True)
class KlineCurveCandidate:
    timestamp: float
    ended_at: float
    window_minutes: int
    direction: str
    stage: str
    open: float
    high: float
    low: float
    close: float
    adverse_move_pct: float
    cumulative_move_pct: float
    cumulative_abs_move_pct: float
    rebound_ratio: float
    curve_score: float
    percentile_score: float
    path_efficiency: float


@dataclass(frozen=True)
class KlineCurveCandidateCluster:
    candidates: tuple[KlineCurveCandidate, ...]
    direction: str
    window_minutes: int
    started_at: float
    ended_at: float


def write_training_labels(date_rows: Iterable[tuple[str, Iterable[dict[str, object]]]], path: str | Path) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for source_date, rows in date_rows:
            for row in rows:
                output = dict(row)
                output.setdefault("source_date", source_date)
                handle.write(json.dumps(output, ensure_ascii=True, sort_keys=True))
                handle.write("\n")
                count += 1
    return count


def batch_replay_training_labels(
    *,
    data_dir: str | Path,
    dates: Iterable[str],
    symbol: str,
    output_labels: str | Path,
    config: PinStageConfig,
    sample_seconds: float = 1.0,
    start_ts: float | None = None,
    end_ts: float | None = None,
    strict: bool = False,
    derive_kline_from_trades_seconds: float = 0.0,
    kline_label_map: dict[float, dict[str, object]] | None = None,
    candidate_signal_scorer: CandidateSignalScorer | None = None,
) -> BatchReplayResult:
    base = Path(data_dir)
    replayed_dates: list[str] = []
    skipped_dates: list[str] = []
    label_count = 0

    with open(output_labels, "w", encoding="utf-8") as handle:
        for date in dates:
            trade_path = base / f"{symbol}{date}.csv.gz"
            orderbook_path = base / f"{date}_{symbol}_ob200.data.zip"
            if not trade_path.exists() or not orderbook_path.exists():
                if strict:
                    raise FileNotFoundError(f"Missing trade/orderbook data for {date}")
                skipped_dates.append(date)
                continue

            events = replay_history(
                trade_path=trade_path,
                orderbook_path=orderbook_path,
                symbol=symbol,
                config=config,
                sample_seconds=sample_seconds,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            if derive_kline_from_trades_seconds > 0.0:
                candles = derive_candles_from_trades(
                    trade_path,
                    symbol=symbol,
                    seconds=derive_kline_from_trades_seconds,
                    start_ts=start_ts,
                    end_ts=end_ts,
                )
                events = annotate_events_with_wick_features(events, candles, config)
            for event in events:
                row = event.to_label()
                row["source_date"] = date
                if kline_label_map:
                    row = annotate_row_with_kline_label(row, kline_label_map)
                if candidate_signal_scorer is not None:
                    row = apply_candidate_signal_scorer(row, candidate_signal_scorer)
                handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True))
                handle.write("\n")
                label_count += 1
            replayed_dates.append(date)

    return BatchReplayResult(
        label_count=label_count,
        replayed_dates=tuple(replayed_dates),
        skipped_dates=tuple(skipped_dates),
    )


def load_kline_label_map(
    path: str | Path,
    config: KlineLabelConfig | None = None,
) -> dict[float, dict[str, object]]:
    labels: dict[float, dict[str, object]] = {}
    for row in label_candles(load_binance_klines_csv(path), config or KlineLabelConfig()):
        timestamp = positive_float(row.get("timestamp"))
        if timestamp is not None:
            labels[timestamp - (timestamp % 60.0)] = row
    return labels


def annotate_row_with_kline_label(
    row: dict[str, object],
    labels: dict[float, dict[str, object]],
    *,
    seconds: float = 60.0,
) -> dict[str, object]:
    timestamp_ms = numeric_float(row.get("timestamp_ms"))
    timestamp = timestamp_ms / 1000.0 if timestamp_ms is not None else row_timestamp(row)
    candle_ts = timestamp - (timestamp % seconds)
    label = labels.get(candle_ts)
    if label is None:
        return row
    annotated = dict(row)
    for key, value in label.items():
        if key == "timestamp":
            annotated["kline_timestamp_ms"] = int(round(float(value) * 1000))
            continue
        annotated[key] = value
    return annotated


def kline_curve_candidates(
    candles: list[dict[str, float]],
    config: KlineCurveCandidateConfig | None = None,
    label_config: KlineLabelConfig | None = None,
) -> list[KlineCurveCandidate]:
    cfg = config or KlineCurveCandidateConfig()
    label_cfg = label_config or KlineLabelConfig()
    sorted_candles = sorted(candles, key=lambda item: item["timestamp"])
    candidates: list[KlineCurveCandidate] = []
    for window in cfg.windows_minutes:
        if window <= 0:
            continue
        history: list[float] = []
        for end_index in range(window - 1, len(sorted_candles)):
            segment = sorted_candles[end_index - window + 1 : end_index + 1]
            aggregate = aggregate_kline_segment(segment)
            label = label_candle_for_candidate(aggregate, label_cfg)
            direction = label.get("kline_direction")
            stage = label.get("kline_stage")
            if direction not in {"down_flush", "up_squeeze"}:
                history.append(curve_adverse_move(aggregate, "down_flush"))
                history = history[-cfg.history_size :]
                continue
            if str(direction) not in cfg.directions:
                history.append(curve_adverse_move(aggregate, str(direction)))
                history = history[-cfg.history_size :]
                continue
            adverse = curve_adverse_move(aggregate, str(direction))
            cumulative_move = aggregate["close"] / aggregate["open"] - 1.0 if aggregate["open"] > 0.0 else 0.0
            cumulative_abs_move = cumulative_abs_segment_move(segment)
            rebound_ratio = float(label.get("kline_rebound_ratio") or 0.0)
            percentile = percentile_rank(adverse, history)
            path_efficiency = min(adverse / max(cumulative_abs_move, 1e-12), 1.0)
            curve_score = clamp01(0.60 * percentile + 0.25 * rebound_ratio + 0.15 * path_efficiency)
            history.append(adverse)
            history = history[-cfg.history_size :]
            if stage not in {"down_wick", "up_wick"} or curve_score < cfg.min_curve_score:
                continue
            candidates.append(
                KlineCurveCandidate(
                    timestamp=float(aggregate["timestamp"]),
                    ended_at=float(aggregate["timestamp"]) + window * 60.0,
                    window_minutes=window,
                    direction=str(direction),
                    stage=str(stage),
                    open=float(aggregate["open"]),
                    high=float(aggregate["high"]),
                    low=float(aggregate["low"]),
                    close=float(aggregate["close"]),
                    adverse_move_pct=adverse,
                    cumulative_move_pct=cumulative_move,
                    cumulative_abs_move_pct=cumulative_abs_move,
                    rebound_ratio=rebound_ratio,
                    curve_score=curve_score,
                    percentile_score=percentile,
                    path_efficiency=path_efficiency,
                )
            )
    return candidates


def append_kline_curve_candidate_rows(
    rows: list[dict[str, object]],
    candidates: list[KlineCurveCandidate],
    *,
    config: PinStageConfig,
    scorer: CandidateSignalScorer | None = None,
    candidate_config: KlineCurveCandidateConfig | None = None,
) -> list[dict[str, object]]:
    if not rows or not candidates:
        return rows
    cfg = candidate_config or KlineCurveCandidateConfig()
    sorted_rows = sorted(rows, key=row_timestamp)
    timestamps = [row_timestamp(row) for row in sorted_rows]
    appended: list[dict[str, object]] = []
    existing_signal_ids = {label_signal_id(row) for row in sorted_rows if row.get("order_action")}
    for cluster in cluster_kline_curve_candidates(candidates, cfg):
        candidate_row = select_kline_curve_cluster_row(
            sorted_rows,
            timestamps,
            cluster,
            config=config,
            scorer=scorer,
            candidate_config=cfg,
        )
        if candidate_row is None:
            continue
        signal_id = label_signal_id(candidate_row)
        if candidate_row.get("order_action"):
            if signal_id in existing_signal_ids:
                continue
            existing_signal_ids.add(signal_id)
        appended.append(candidate_row)
    if not appended:
        return sorted_rows
    return sorted(sorted_rows + appended, key=row_timestamp)


def cluster_kline_curve_candidates(
    candidates: list[KlineCurveCandidate],
    config: KlineCurveCandidateConfig,
) -> list[KlineCurveCandidateCluster]:
    clusters: list[KlineCurveCandidateCluster] = []
    active: list[KlineCurveCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.direction, item.window_minutes, item.timestamp)):
        if not active:
            active = [candidate]
            continue
        previous = active[-1]
        same_group = (
            candidate.direction == previous.direction
            and candidate.window_minutes == previous.window_minutes
            and candidate.timestamp - previous.timestamp <= config.cluster_seconds
        )
        if same_group:
            active.append(candidate)
        else:
            clusters.append(build_kline_curve_candidate_cluster(active))
            active = [candidate]
    if active:
        clusters.append(build_kline_curve_candidate_cluster(active))
    return sorted(clusters, key=lambda item: item.ended_at)


def build_kline_curve_candidate_cluster(candidates: list[KlineCurveCandidate]) -> KlineCurveCandidateCluster:
    first = candidates[0]
    return KlineCurveCandidateCluster(
        candidates=tuple(candidates),
        direction=first.direction,
        window_minutes=first.window_minutes,
        started_at=min(candidate.timestamp for candidate in candidates),
        ended_at=max(candidate.ended_at for candidate in candidates),
    )


def select_kline_curve_cluster_row(
    rows: list[dict[str, object]],
    timestamps: list[float],
    cluster: KlineCurveCandidateCluster,
    *,
    config: PinStageConfig,
    scorer: CandidateSignalScorer | None,
    candidate_config: KlineCurveCandidateConfig,
) -> dict[str, object] | None:
    start = bisect.bisect_left(timestamps, min(candidate.ended_at for candidate in cluster.candidates))
    end_ts = cluster.ended_at + max(candidate_config.lookahead_seconds, 0.0)
    window_rows: list[dict[str, object]] = []
    for row in rows[start:]:
        ts = row_timestamp(row)
        if ts > end_ts:
            break
        if positive_float(row.get("price")) is not None:
            window_rows.append(row)
    if not window_rows:
        return None
    max_notional = max((positive_float(row.get("trade_notional")) or 0.0 for row in window_rows), default=0.0)
    best_row: dict[str, object] | None = None
    best_score = -1.0
    for candidate in cluster.candidates:
        for row in window_rows:
            ts = row_timestamp(row)
            if ts < candidate.ended_at or ts > candidate.ended_at + max(candidate_config.lookahead_seconds, 0.0):
                continue
            if not kline_candidate_price_confirmation(row, candidate):
                continue
            candidate_row = build_kline_curve_candidate_row(row, candidate, config)
            if scorer is not None:
                candidate_row = apply_candidate_signal_scorer(candidate_row, scorer)
                if not candidate_row.get("candidate_signal_passed"):
                    continue
            cluster_score = kline_cluster_row_score(candidate_row, candidate, max_notional)
            if cluster_score < candidate_config.min_cluster_score:
                continue
            candidate_row.update(
                {
                    "candidate_cluster_started_at": isoformat_seconds(cluster.started_at),
                    "candidate_cluster_ended_at": isoformat_seconds(cluster.ended_at),
                    "candidate_cluster_size": len(cluster.candidates),
                    "candidate_cluster_score": cluster_score,
                    "candidate_cluster_max_trade_notional": max_notional,
                }
            )
            if cluster_score > best_score:
                best_score = cluster_score
                best_row = candidate_row
    return best_row


def select_kline_candidate_confirmation_row(
    rows: list[dict[str, object]],
    timestamps: list[float],
    candidate: KlineCurveCandidate,
    lookahead_seconds: float,
) -> dict[str, object] | None:
    start = bisect.bisect_left(timestamps, candidate.ended_at)
    end_ts = candidate.ended_at + max(lookahead_seconds, 0.0)
    for row in rows[start:]:
        ts = row_timestamp(row)
        if ts > end_ts:
            break
        price = positive_float(row.get("price"))
        if price is None:
            continue
        if candidate.direction == "down_flush" and price >= candidate.low:
            return row
        if candidate.direction == "up_squeeze" and price <= candidate.high:
            return row
    return None


def kline_candidate_price_confirmation(row: dict[str, object], candidate: KlineCurveCandidate) -> bool:
    price = positive_float(row.get("price"))
    if price is None:
        return False
    if candidate.direction == "down_flush":
        return price >= candidate.low
    if candidate.direction == "up_squeeze":
        return price <= candidate.high
    return False


def kline_cluster_row_score(
    row: dict[str, object],
    candidate: KlineCurveCandidate,
    max_notional: float,
) -> float:
    price = positive_float(row.get("price")) or candidate.close
    trade_notional = positive_float(row.get("trade_notional")) or 0.0
    volume_score = trade_notional / max_notional if max_notional > 0.0 else 0.0
    signal_score = min(positive_float(row.get("candidate_signal_score")) or 0.0, 2.0) / 2.0
    price_score = kline_cluster_price_score(price, candidate)
    return clamp01(
        0.35 * candidate.curve_score
        + 0.25 * signal_score
        + 0.25 * price_score
        + 0.15 * min(volume_score, 1.0)
    )


def kline_cluster_price_score(price: float, candidate: KlineCurveCandidate) -> float:
    if candidate.direction == "down_flush":
        span = max(candidate.close - candidate.low, 1e-12)
        near_extreme = clamp01((candidate.close - price) / span)
        rebound = clamp01((price - candidate.low) / span)
    else:
        span = max(candidate.high - candidate.close, 1e-12)
        near_extreme = clamp01((price - candidate.close) / span)
        rebound = clamp01((candidate.high - price) / span)
    return clamp01(0.55 * near_extreme + 0.45 * rebound)


def build_kline_curve_candidate_row(
    row: dict[str, object],
    candidate: KlineCurveCandidate,
    config: PinStageConfig,
) -> dict[str, object]:
    output = dict(row)
    price = positive_float(output.get("price")) or candidate.close
    extreme = candidate.low if candidate.direction == "down_flush" else candidate.high
    strength = max(positive_float(output.get("strength")) or 0.0, candidate.curve_score)
    order_plan = build_entry_order_plan(
        stage=Stage.REBOUND_CONFIRMED,
        direction=candidate.direction,  # type: ignore[arg-type]
        price=price,
        extreme_price=extreme,
        strength=strength,
        config=config,
    )
    output.update(
        {
            "candidate_source": "kline_curve",
            "stage": "rebound_confirmed",
            "direction": candidate.direction,
            "started_at": isoformat_seconds(candidate.timestamp),
            "ended_at": isoformat_seconds(candidate.ended_at),
            "entry_candidate_price": price,
            "strength": strength,
            "rebound_ratio": candidate.rebound_ratio,
            "kline_curve_window_minutes": candidate.window_minutes,
            "kline_curve_stage": candidate.stage,
            "kline_curve_score": candidate.curve_score,
            "kline_curve_percentile_score": candidate.percentile_score,
            "kline_curve_path_efficiency": candidate.path_efficiency,
            "kline_curve_adverse_move_pct": candidate.adverse_move_pct,
            "kline_curve_cumulative_move_pct": candidate.cumulative_move_pct,
            "kline_curve_cumulative_abs_move_pct": candidate.cumulative_abs_move_pct,
            "kline_curve_open": candidate.open,
            "kline_curve_high": candidate.high,
            "kline_curve_low": candidate.low,
            "kline_curve_close": candidate.close,
        }
    )
    if order_plan is not None:
        output.update(
            {
                "order_action": order_plan.order_action,
                "entry_side": order_plan.entry_side,
                "entry_order_allocation_ratio": order_plan.allocation_ratio,
                "entry_order_notional": order_plan.total_notional,
                "entry_order_slices": list(order_plan.slices),
            }
        )
    return output


def aggregate_kline_segment(segment: list[dict[str, float]]) -> dict[str, float]:
    first = segment[0]
    last = segment[-1]
    return {
        "timestamp": float(first["timestamp"]),
        "open": float(first["open"]),
        "high": max(float(item["high"]) for item in segment),
        "low": min(float(item["low"]) for item in segment),
        "close": float(last["close"]),
        "volume": sum(float(item.get("volume") or 0.0) for item in segment),
        "quote_volume": sum(float(item.get("quote_volume") or 0.0) for item in segment),
        "trade_count": sum(float(item.get("trade_count") or 0.0) for item in segment),
        "taker_buy_quote_volume": sum(float(item.get("taker_buy_quote_volume") or 0.0) for item in segment),
    }


def label_candle_for_candidate(candle: dict[str, float], config: KlineLabelConfig) -> dict[str, object]:
    return next(label_candles([candle], config))


def cumulative_abs_segment_move(segment: list[dict[str, float]]) -> float:
    total = 0.0
    previous = float(segment[0]["open"])
    for candle in segment:
        close = float(candle["close"])
        if previous > 0.0:
            total += abs(close / previous - 1.0)
        previous = close
    return total


def curve_adverse_move(candle: dict[str, float], direction: str) -> float:
    open_price = float(candle["open"])
    if open_price <= 0.0:
        return 0.0
    if direction == "up_squeeze":
        return max(float(candle["high"]) / open_price - 1.0, 0.0)
    return max(1.0 - float(candle["low"]) / open_price, 0.0)


def percentile_rank(value: float, history: list[float]) -> float:
    if not history:
        return 1.0
    sorted_history = sorted(history)
    return bisect.bisect_right(sorted_history, value) / len(sorted_history)


def clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))


def apply_candidate_signal_scorer(
    row: dict[str, object],
    scorer: CandidateSignalScorer,
) -> dict[str, object]:
    annotated = dict(row)
    direction = str(row.get("direction") or "")
    signal_thresholds = candidate_thresholds_for_direction(direction, scorer)
    if signal_thresholds is None:
        return annotated
    signal_score = score_learned_candidate_signal(
        annotated,
        signal_thresholds,
        direction=direction,
        min_score=scorer.min_candidate_score,
    )
    trend_score = (
        score_learned_candidate_signal(
            annotated,
            scorer.trend_break,
            direction=direction,
            min_score=scorer.min_trend_score,
        )
        if scorer.trend_break is not None
        else None
    )
    trend_score_passed = trend_score is not None and trend_score.passed
    trend_blocked = trend_score_passed and trend_filter_context_blocks(annotated)
    passed = signal_score.passed and not trend_blocked
    annotated.update(
        {
            "candidate_signal_score": signal_score.score,
            "candidate_signal_passed": passed,
            "candidate_signal_target_stage": signal_score.target_stage,
            "candidate_signal_window_seconds": signal_score.best_window_seconds,
            "candidate_signal_move_ratio": signal_score.move_ratio,
            "candidate_signal_notional_ratio": signal_score.notional_ratio,
            "candidate_signal_imbalance_ratio": signal_score.imbalance_ratio,
            "trend_filter_score": trend_score.score if trend_score is not None else 0.0,
            "trend_filter_score_passed": trend_score_passed,
            "trend_filter_blocked": trend_blocked,
            "trend_filter_passed": not trend_blocked,
            "trend_filter_window_seconds": trend_score.best_window_seconds if trend_score is not None else None,
        }
    )
    if should_gate_candidate_order(annotated) and not passed:
        annotated["order_action"] = None
        annotated["entry_side"] = None
        annotated["entry_order_allocation_ratio"] = 0.0
        annotated["entry_order_notional"] = 0.0
        annotated["entry_order_slices"] = []
    return annotated


def candidate_thresholds_for_direction(
    direction: str,
    scorer: CandidateSignalScorer,
) -> dict[str, object] | None:
    if direction == "down_flush":
        return scorer.down_wick
    if direction == "up_squeeze":
        return scorer.up_wick
    return None


def should_gate_candidate_order(row: dict[str, object]) -> bool:
    return row.get("stage") == "rebound_confirmed" and row.get("order_action") in {"buy", "sell"}


def trend_filter_context_blocks(row: dict[str, object]) -> bool:
    if row.get("stage") == "trend_break":
        return True
    rebound_ratio = numeric_float(row.get("rebound_ratio"))
    return rebound_ratio is not None and rebound_ratio < 0.25


def compare_label_backtests(
    rows: Iterable[dict[str, object]],
    config: LabelBacktestConfig,
) -> LabelBacktestComparison:
    rows_list = sorted(rows, key=row_timestamp)
    return LabelBacktestComparison(
        fixed=run_label_backtest(rows_list, config, strategy="fixed"),
        adaptive=run_label_backtest(rows_list, config, strategy="adaptive"),
    )


def compare_slow_wick_backtests(
    rows: Iterable[dict[str, object]],
    config: LabelBacktestConfig,
) -> LabelBacktestComparison:
    rows_list = sorted(rows, key=row_timestamp)
    return LabelBacktestComparison(
        fixed=run_slow_wick_backtest(rows_list, config, strategy="fixed"),
        adaptive=run_slow_wick_backtest(rows_list, config, strategy="adaptive"),
    )


def run_label_backtest(
    rows: Iterable[dict[str, object]],
    config: LabelBacktestConfig,
    *,
    strategy: Strategy,
) -> LabelBacktestResult:
    result = LabelBacktestResult(strategy=strategy, config=config, cash=config.initial_cash)
    seen_signals: set[str] = set()
    pending: list[PendingSlice] = []
    final_timestamp = ""

    for row in rows:
        price = positive_float(row.get("price"))
        timestamp = str(row.get("timestamp") or "")
        if price is None or not timestamp:
            continue
        ts = parse_timestamp(timestamp)
        result.last_price = price
        final_timestamp = timestamp

        pending = execute_pending_slices(result, pending, row, ts, price, config)
        close_profitable_positions(result, timestamp, price, config)

        signal_id = label_signal_id(row)
        if row.get("order_action") == "sell":
            close_all_positions(result, timestamp, price, reason="opposite_rebound_signal")
            continue
        if row.get("order_action") != "buy" or row.get("stage") != "rebound_confirmed":
            continue
        if signal_id in seen_signals:
            continue
        seen_signals.add(signal_id)

        if strategy == "fixed":
            execute_buy(result, timestamp, ts, price, config.fixed_order_notional, config, signal_id, "fixed_label_entry")
        else:
            immediate, deferred = adaptive_slices(row, ts, signal_id)
            for item in immediate:
                execute_buy(result, timestamp, ts, price, item.notional, config, signal_id, "adaptive_label_entry")
            pending.extend(deferred)

    if result.open_positions and result.last_price > 0.0:
        close_all_positions(result, final_timestamp, result.last_price, reason="final_mark")
    return result


def run_slow_wick_backtest(
    rows: Iterable[dict[str, object]],
    config: LabelBacktestConfig,
    *,
    strategy: Strategy,
) -> LabelBacktestResult:
    result = LabelBacktestResult(strategy=strategy, config=config, cash=config.initial_cash)
    seen_signals: set[str] = set()
    final_timestamp = ""

    for row in rows:
        price = positive_float(row.get("price"))
        timestamp = str(row.get("timestamp") or "")
        if price is None or not timestamp:
            continue
        ts = parse_timestamp(timestamp)
        result.last_price = price
        final_timestamp = timestamp
        close_profitable_positions(result, timestamp, price, config)

        if row.get("slow_wick_stage") != "slow_rebound_confirmed":
            continue
        if row.get("slow_wick_direction") == "up_squeeze":
            close_all_positions(result, timestamp, price, reason="slow_wick_opposite_signal")
            continue
        if row.get("slow_wick_direction") != "down_flush":
            continue
        if not slow_wick_entry_filter_passed(row, config):
            continue

        signal_id = slow_wick_signal_id(row)
        if signal_id in seen_signals:
            continue
        seen_signals.add(signal_id)
        notional = (
            config.fixed_order_notional
            if strategy == "fixed"
            else slow_wick_adaptive_notional(row, config)
        )
        execute_buy(result, timestamp, ts, price, notional, config, signal_id, f"{strategy}_slow_wick_entry")

    if result.open_positions and result.last_price > 0.0:
        close_all_positions(result, final_timestamp, result.last_price, reason="final_mark")
    return result


def execute_pending_slices(
    result: LabelBacktestResult,
    pending: list[PendingSlice],
    row: dict[str, object],
    ts: float,
    price: float,
    config: LabelBacktestConfig,
) -> list[PendingSlice]:
    timestamp = str(row.get("timestamp") or "")
    remaining: list[PendingSlice] = []
    for item in pending:
        if ts - item.created_ts > config.pending_slice_ttl_seconds:
            continue
        if price <= item.price:
            execute_buy(result, timestamp, ts, item.price, item.notional, config, item.signal_id, "adaptive_retest_slice")
        else:
            remaining.append(item)
    return remaining


def execute_buy(
    result: LabelBacktestResult,
    timestamp: str,
    ts: float,
    price: float,
    notional: float,
    config: LabelBacktestConfig,
    signal_id: str,
    reason: str,
) -> None:
    if notional <= 0.0 or price <= 0.0:
        return
    if len(result.open_positions) >= config.max_open_positions:
        return
    fee = notional * config.fee_rate
    total_cost = notional + fee
    if result.cash < total_cost:
        return
    quantity = notional / price
    result.cash -= total_cost
    result.open_positions.append(
        LabelPosition(
            entry_time=timestamp,
            entry_ts=ts,
            entry_price=price,
            quantity=quantity,
            notional=notional,
            fee=fee,
            signal_id=signal_id,
        )
    )
    result.trades.append(
        LabelTrade(
            timestamp=timestamp,
            side="buy",
            price=price,
            quantity=quantity,
            notional=notional,
            fee=fee,
            realized_pnl=0.0,
            reason=reason,
            signal_id=signal_id,
        )
    )


def close_profitable_positions(
    result: LabelBacktestResult,
    timestamp: str,
    price: float,
    config: LabelBacktestConfig,
) -> None:
    remaining: list[LabelPosition] = []
    for position in result.open_positions:
        if price >= position.entry_price * (1.0 + config.take_profit_pct):
            close_position(result, position, timestamp, price, reason="take_profit")
        else:
            remaining.append(position)
    result.open_positions = remaining


def close_all_positions(
    result: LabelBacktestResult,
    timestamp: str,
    price: float,
    *,
    reason: str,
) -> None:
    for position in result.open_positions:
        close_position(result, position, timestamp, price, reason=reason)
    result.open_positions = []


def close_position(
    result: LabelBacktestResult,
    position: LabelPosition,
    timestamp: str,
    price: float,
    *,
    reason: str,
) -> None:
    notional = position.quantity * price
    fee = notional * result.config.fee_rate
    realized_pnl = notional - fee - position.notional - position.fee
    result.cash += notional - fee
    result.trades.append(
        LabelTrade(
            timestamp=timestamp,
            side="sell",
            price=price,
            quantity=position.quantity,
            notional=notional,
            fee=fee,
            realized_pnl=realized_pnl,
            reason=reason,
            signal_id=position.signal_id,
        )
    )


def adaptive_slices(row: dict[str, object], ts: float, signal_id: str) -> tuple[list[PendingSlice], list[PendingSlice]]:
    slices = row.get("entry_order_slices")
    if not isinstance(slices, list):
        return ([], [])
    immediate: list[PendingSlice] = []
    deferred: list[PendingSlice] = []
    for index, raw in enumerate(slices):
        if not isinstance(raw, dict):
            continue
        price = positive_float(raw.get("price"))
        notional = positive_float(raw.get("notional"))
        if price is None or notional is None:
            continue
        item = PendingSlice(created_ts=ts, signal_id=signal_id, price=price, notional=notional)
        if index == 0 or raw.get("trigger") == "rebound_confirmed":
            immediate.append(item)
        else:
            deferred.append(item)
    return immediate, deferred


def read_jsonl(path: str | Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl_rows(rows: Iterable[dict[str, object]], path: str | Path) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def write_summary_json(
    *,
    batch: BatchReplayResult,
    comparison: LabelBacktestComparison,
    slow_wick_comparison: LabelBacktestComparison | None = None,
    path: str | Path,
) -> None:
    payload = {
        "batch": batch.to_dict(),
        "comparison": comparison.to_dict(),
    }
    if slow_wick_comparison is not None:
        payload["slow_wick_comparison"] = slow_wick_comparison.to_dict()
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def label_signal_id(row: dict[str, object]) -> str:
    return "|".join(
        [
            str(row.get("source_date") or ""),
            str(row.get("started_at") or row.get("timestamp") or ""),
            str(row.get("stage") or ""),
            str(row.get("direction") or ""),
            str(row.get("order_action") or ""),
        ]
    )


def slow_wick_signal_id(row: dict[str, object]) -> str:
    timestamp = row.get("slow_wick_timestamp")
    if not timestamp:
        timestamp = "|".join(
            str(row.get(key) or "")
            for key in ("slow_wick_open", "slow_wick_high", "slow_wick_low", "slow_wick_close")
        )
    if not timestamp:
        timestamp = row.get("timestamp") or ""
    return "|".join(
        [
            str(row.get("source_date") or ""),
            str(timestamp),
            str(row.get("slow_wick_stage") or ""),
            str(row.get("slow_wick_direction") or ""),
        ]
    )


def slow_wick_adaptive_notional(row: dict[str, object], config: LabelBacktestConfig) -> float:
    range_pct = positive_float(row.get("slow_wick_range_pct")) or 0.0
    rebound_ratio = positive_float(row.get("slow_wick_rebound_ratio")) or 0.0
    range_score = max(0.0, min(range_pct / 0.01, 1.0))
    rebound_score = max(0.0, min(rebound_ratio, 1.0))
    allocation = range_score * rebound_score
    high = max(config.fixed_order_notional, config.max_entry_order_notional)
    return config.fixed_order_notional + allocation * (high - config.fixed_order_notional)


def slow_wick_entry_filter_passed(row: dict[str, object], config: LabelBacktestConfig) -> bool:
    book_imbalance = numeric_float(row.get("book_imbalance"))
    trade_imbalance = numeric_float(row.get("trade_imbalance"))
    price = positive_float(row.get("price"))
    mid_price = positive_float(row.get("mid_price"))
    if book_imbalance is None or book_imbalance < config.slow_wick_min_book_imbalance:
        return False
    if trade_imbalance is None or trade_imbalance < config.slow_wick_min_trade_imbalance:
        return False
    if row.get("trade_outside_book") == "below_bid":
        return False
    if config.slow_wick_require_price_at_or_above_mid:
        if price is None or mid_price is None or price < mid_price:
            return False
    if config.slow_wick_require_trend_filter and row.get("slow_wick_trend_filter_passed") is False:
        return False
    return True


def row_timestamp(row: dict[str, object]) -> float:
    timestamp = str(row.get("timestamp") or "")
    return parse_timestamp(timestamp) if timestamp else 0.0


def positive_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0.0 else None


def numeric_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_dates(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_candidate_directions(value: str) -> tuple[str, ...]:
    directions = tuple(item.strip() for item in value.split(",") if item.strip())
    invalid = [item for item in directions if item not in VALID_KLINE_CANDIDATE_DIRECTIONS]
    if invalid:
        raise ValueError(f"Unsupported kline candidate direction(s): {','.join(invalid)}")
    return directions or DEFAULT_KLINE_CANDIDATE_DIRECTIONS


def default_dates() -> str:
    return ",".join(
        [
            "2025-10-09",
            "2025-10-10",
            "2025-10-11",
            "2026-02-04",
            "2026-02-05",
            "2026-02-06",
            "2026-05-01",
            "2026-05-02",
            "2026-05-03",
            "2026-05-04",
            "2026-05-05",
            "2026-05-06",
            "2026-05-07",
        ]
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch replay BTCUSDT pin labels and compare fixed/adaptive sizing.")
    parser.add_argument("--data-dir", default="by_data")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--dates", default=default_dates())
    parser.add_argument("--calibration-json", default="btcusdt_extreme_standard_2025-10_2026-02.json")
    parser.add_argument("--learned-thresholds-json", help="Optional supervised threshold JSON from pin_threshold_learning.py.")
    parser.add_argument("--down-wick-signal-thresholds-json", help="Optional learned down_wick JSON used as a buy candidate scorer.")
    parser.add_argument("--up-wick-signal-thresholds-json", help="Optional learned up_wick JSON used as a sell candidate scorer.")
    parser.add_argument("--trend-break-filter-thresholds-json", help="Optional learned trend_break JSON used as a candidate order filter.")
    parser.add_argument("--min-candidate-signal-score", type=float, default=1.0)
    parser.add_argument("--min-trend-filter-score", type=float, default=1.0)
    parser.add_argument("--output-labels", default="btcusdt_pin_training_labels.jsonl")
    parser.add_argument("--output-summary", default="btcusdt_pin_label_backtest_summary.json")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--sample-seconds", type=float, default=1.0)
    parser.add_argument(
        "--derive-kline-from-trades-seconds",
        type=float,
        default=0.0,
        help="Diagnostic fallback: derive auxiliary candles from publicTrade for slow wick labels, e.g. 900 for 15m.",
    )
    parser.add_argument("--binance-kline-csv", help="Optional Binance 1m kline CSV used to attach kline_stage labels.")
    parser.add_argument(
        "--enable-kline-curve-candidates",
        action="store_true",
        help="Use Binance 1m kline curve labels as an independent candidate source before trade/OB selection.",
    )
    parser.add_argument(
        "--kline-candidate-windows",
        default="1,15",
        help="Comma-separated kline aggregation windows in minutes used by the independent candidate source.",
    )
    parser.add_argument(
        "--kline-candidate-directions",
        default=",".join(DEFAULT_KLINE_CANDIDATE_DIRECTIONS),
        help="Comma-separated candidate directions: down_flush, up_squeeze, or both. Default is long-only down_flush.",
    )
    parser.add_argument(
        "--kline-candidate-min-curve-score",
        type=float,
        default=0.80,
        help="Minimum dynamic curve score for 1m/aggregated kline candidates.",
    )
    parser.add_argument(
        "--kline-candidate-lookahead-seconds",
        type=float,
        default=180.0,
        help="Seconds after a kline candidate where trade/OB rows may be selected as the execution point.",
    )
    parser.add_argument(
        "--kline-candidate-history-size",
        type=int,
        default=240,
        help="Number of prior aggregated kline moves used for percentile scoring.",
    )
    parser.add_argument(
        "--kline-candidate-cluster-seconds",
        type=float,
        default=900.0,
        help="Merge nearby same-direction kline candidates into one cluster over this many seconds.",
    )
    parser.add_argument(
        "--kline-candidate-min-cluster-score",
        type=float,
        default=0.50,
        help="Minimum combined kline/trade/OB score required after candidate clustering.",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--window-seconds", type=float, default=5.0)
    parser.add_argument("--feature-windows", default="1,5,15,30,45,60,120,180,240")
    parser.add_argument("--trigger-windows", default="5,15,30,45,60,120,180,240")
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
    parser.add_argument("--max-book-lag-ms", type=float, default=500.0)
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--fixed-order-notional", type=float, default=3_000.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.006)
    parser.add_argument("--fee-rate", type=float, default=0.0006)
    parser.add_argument("--max-open-positions", type=int, default=30)
    parser.add_argument("--pending-slice-ttl-seconds", type=float, default=180.0)
    parser.add_argument("--slow-wick-min-book-imbalance", type=float, default=0.0)
    parser.add_argument("--slow-wick-min-trade-imbalance", type=float, default=-0.10)
    parser.add_argument("--disable-slow-wick-price-mid-filter", action="store_true")
    parser.add_argument("--disable-slow-wick-trend-filter", action="store_true")
    return parser.parse_args(argv)


def build_replay_config_from_args(args: argparse.Namespace) -> PinStageConfig:
    replay_config = PinStageConfig(
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
    calibration_path = Path(args.calibration_json)
    if calibration_path.exists():
        replay_config = apply_extreme_standard(replay_config, calibration_path)
    if args.learned_thresholds_json:
        learned = json.loads(Path(args.learned_thresholds_json).read_text(encoding="utf-8"))
        replay_config = apply_learned_thresholds(replay_config, learned)
    return replay_config


def build_candidate_signal_scorer_from_args(args: argparse.Namespace) -> CandidateSignalScorer | None:
    down_wick = load_optional_json(args.down_wick_signal_thresholds_json)
    up_wick = load_optional_json(args.up_wick_signal_thresholds_json)
    trend_break = load_optional_json(args.trend_break_filter_thresholds_json)
    if down_wick is None and up_wick is None and trend_break is None:
        return None
    return CandidateSignalScorer(
        down_wick=down_wick,
        up_wick=up_wick,
        trend_break=trend_break,
        min_candidate_score=args.min_candidate_signal_score,
        min_trend_score=args.min_trend_filter_score,
    )


def load_optional_json(path: str | None) -> dict[str, object] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return payload


def build_kline_curve_candidate_config_from_args(args: argparse.Namespace) -> KlineCurveCandidateConfig | None:
    if not args.enable_kline_curve_candidates:
        return None
    return KlineCurveCandidateConfig(
        windows_minutes=tuple(int(window) for window in parse_windows_arg(args.kline_candidate_windows)),
        directions=parse_candidate_directions(args.kline_candidate_directions),
        min_curve_score=args.kline_candidate_min_curve_score,
        lookahead_seconds=args.kline_candidate_lookahead_seconds,
        history_size=args.kline_candidate_history_size,
        cluster_seconds=args.kline_candidate_cluster_seconds,
        min_cluster_score=args.kline_candidate_min_cluster_score,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    start_ts = parse_timestamp(args.start) if args.start else None
    end_ts = parse_timestamp(args.end) if args.end else None
    replay_config = build_replay_config_from_args(args)
    candidate_signal_scorer = build_candidate_signal_scorer_from_args(args)
    kline_curve_candidate_config = build_kline_curve_candidate_config_from_args(args)

    batch = batch_replay_training_labels(
        data_dir=args.data_dir,
        dates=parse_dates(args.dates),
        symbol=args.symbol,
        output_labels=args.output_labels,
        config=replay_config,
        sample_seconds=args.sample_seconds,
        start_ts=start_ts,
        end_ts=end_ts,
        strict=args.strict,
        derive_kline_from_trades_seconds=args.derive_kline_from_trades_seconds,
        kline_label_map=load_kline_label_map(args.binance_kline_csv) if args.binance_kline_csv else None,
        candidate_signal_scorer=candidate_signal_scorer,
    )
    rows = read_jsonl(args.output_labels)
    if kline_curve_candidate_config is not None:
        if not args.binance_kline_csv:
            raise ValueError("--enable-kline-curve-candidates requires --binance-kline-csv")
        candles = load_binance_klines_csv(args.binance_kline_csv)
        candidates = kline_curve_candidates(candles, kline_curve_candidate_config)
        rows = append_kline_curve_candidate_rows(
            rows,
            candidates,
            config=replay_config,
            scorer=candidate_signal_scorer,
            candidate_config=kline_curve_candidate_config,
        )
        batch = BatchReplayResult(
            label_count=write_jsonl_rows(rows, args.output_labels),
            replayed_dates=batch.replayed_dates,
            skipped_dates=batch.skipped_dates,
        )
    comparison = compare_label_backtests(
        rows,
        LabelBacktestConfig(
            initial_cash=args.initial_cash,
            fixed_order_notional=args.fixed_order_notional,
            max_entry_order_notional=args.max_entry_order_notional,
            take_profit_pct=args.take_profit_pct,
            fee_rate=args.fee_rate,
            max_open_positions=args.max_open_positions,
            pending_slice_ttl_seconds=args.pending_slice_ttl_seconds,
            slow_wick_min_book_imbalance=args.slow_wick_min_book_imbalance,
            slow_wick_min_trade_imbalance=args.slow_wick_min_trade_imbalance,
            slow_wick_require_price_at_or_above_mid=not args.disable_slow_wick_price_mid_filter,
            slow_wick_require_trend_filter=not args.disable_slow_wick_trend_filter,
        ),
    )
    slow_wick_comparison = compare_slow_wick_backtests(
        rows,
        LabelBacktestConfig(
            initial_cash=args.initial_cash,
            fixed_order_notional=args.fixed_order_notional,
            max_entry_order_notional=args.max_entry_order_notional,
            take_profit_pct=args.take_profit_pct,
            fee_rate=args.fee_rate,
            max_open_positions=args.max_open_positions,
            pending_slice_ttl_seconds=args.pending_slice_ttl_seconds,
            slow_wick_min_book_imbalance=args.slow_wick_min_book_imbalance,
            slow_wick_min_trade_imbalance=args.slow_wick_min_trade_imbalance,
            slow_wick_require_price_at_or_above_mid=not args.disable_slow_wick_price_mid_filter,
            slow_wick_require_trend_filter=not args.disable_slow_wick_trend_filter,
        ),
    )
    write_summary_json(
        batch=batch,
        comparison=comparison,
        slow_wick_comparison=slow_wick_comparison,
        path=args.output_summary,
    )
    print(f"labels: {args.output_labels} ({batch.label_count})")
    print(f"summary: {args.output_summary}")
    print(f"replayed dates: {','.join(batch.replayed_dates)}")
    if batch.skipped_dates:
        print(f"skipped dates: {','.join(batch.skipped_dates)}")
    print(
        "fixed final_equity={:.2f} realized_pnl={:.2f} entries={} notional={:.2f}".format(
            comparison.fixed.final_equity,
            comparison.fixed.realized_pnl,
            comparison.fixed.entry_count,
            comparison.fixed.total_entry_notional,
        )
    )
    print(
        "adaptive final_equity={:.2f} realized_pnl={:.2f} entries={} notional={:.2f}".format(
            comparison.adaptive.final_equity,
            comparison.adaptive.realized_pnl,
            comparison.adaptive.entry_count,
            comparison.adaptive.total_entry_notional,
        )
    )
    print(
        "slow_wick fixed final_equity={:.2f} realized_pnl={:.2f} entries={} notional={:.2f}".format(
            slow_wick_comparison.fixed.final_equity,
            slow_wick_comparison.fixed.realized_pnl,
            slow_wick_comparison.fixed.entry_count,
            slow_wick_comparison.fixed.total_entry_notional,
        )
    )
    print(
        "slow_wick adaptive final_equity={:.2f} realized_pnl={:.2f} entries={} notional={:.2f}".format(
            slow_wick_comparison.adaptive.final_equity,
            slow_wick_comparison.adaptive.realized_pnl,
            slow_wick_comparison.adaptive.entry_count,
            slow_wick_comparison.adaptive.total_entry_notional,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
