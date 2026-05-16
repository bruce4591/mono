from __future__ import annotations

import argparse
import json
import mmap
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from market.trade.pin_extreme_calibration import ShockCandidate, scan_trade_file
from market.trade.pin_stage_replay import PinStageConfig, format_window_label, parse_windows_arg


@dataclass(frozen=True)
class LearnedThresholdConfig:
    pre_quantile: float = 0.95
    shock_quantile: float = 0.99
    extreme_quantile: float = 0.999
    notional_quantile: float = 0.95
    imbalance_quantile: float = 0.95
    min_candidates_per_window: int = 3


@dataclass(frozen=True)
class SupervisedThresholdConfig:
    windows: tuple[float, ...] = (5.0, 15.0, 30.0, 45.0, 60.0, 120.0, 180.0, 240.0)
    label_source: str = "1m_kline"
    positive_stage: str = "down_wick"
    pre_shock_scale: float = 0.50
    extreme_quantile: float = 0.90
    min_positive_count: int = 1


@dataclass(frozen=True)
class CandidateSignalScore:
    score: float
    passed: bool
    best_window_seconds: float | None
    target_stage: str | None
    move_ratio: float
    notional_ratio: float
    imbalance_ratio: float


def learn_thresholds_from_candidates(
    candidates: Iterable[ShockCandidate],
    config: LearnedThresholdConfig | None = None,
) -> dict[str, object]:
    cfg = config or LearnedThresholdConfig()
    grouped: dict[float, list[ShockCandidate]] = {}
    candidate_count = 0
    for candidate in candidates:
        grouped.setdefault(candidate.window_seconds, []).append(candidate)
        candidate_count += 1

    thresholds: dict[str, dict[str, object]] = {}
    for window in sorted(grouped):
        rows = grouped[window]
        if len(rows) < cfg.min_candidates_per_window:
            continue
        moves = sorted(candidate.abs_move_pct for candidate in rows)
        notionals = sorted(candidate.total_notional for candidate in rows)
        imbalances = sorted(abs(candidate.trade_imbalance) for candidate in rows)
        pre = quantile(moves, cfg.pre_quantile)
        shock = max(quantile(moves, cfg.shock_quantile), pre)
        extreme = max(quantile(moves, cfg.extreme_quantile), shock)
        thresholds[format_window_label(window)] = {
            "window_seconds": window,
            "sample_count": len(rows),
            "pre_shock_move_pct": pre,
            "shock_move_pct": shock,
            "extreme_move_pct": extreme,
            "notional_threshold": quantile(notionals, cfg.notional_quantile),
            "imbalance_threshold": quantile(imbalances, cfg.imbalance_quantile),
        }

    return {
        "method": "quantile_threshold_learning",
        "candidate_count": candidate_count,
        "config": {
            "pre_quantile": cfg.pre_quantile,
            "shock_quantile": cfg.shock_quantile,
            "extreme_quantile": cfg.extreme_quantile,
            "notional_quantile": cfg.notional_quantile,
            "imbalance_quantile": cfg.imbalance_quantile,
            "min_candidates_per_window": cfg.min_candidates_per_window,
        },
        "thresholds": thresholds,
    }


def learn_thresholds_from_labeled_rows(
    rows: Iterable[dict[str, object]],
    config: SupervisedThresholdConfig | None = None,
) -> dict[str, object]:
    cfg = config or SupervisedThresholdConfig()
    samples_by_window: dict[float, list[dict[str, object]]] = {window: [] for window in cfg.windows}
    sample_count = 0
    positive_count = 0
    for row in rows:
        sample_count += 1
        if is_positive_kline_label(row, cfg):
            positive_count += 1
        for window in cfg.windows:
            sample = supervised_window_sample(row, window, cfg)
            if sample is not None:
                samples_by_window[window].append(sample)

    thresholds: dict[str, dict[str, object]] = {}

    for window in cfg.windows:
        samples = samples_by_window[window]
        positives = [sample for sample in samples if sample["label"]]
        if len(positives) < cfg.min_positive_count:
            continue

        move_threshold, move_metrics = best_binary_threshold(
            [(float(sample["move"]), bool(sample["label"])) for sample in samples]
        )
        notional_threshold, notional_metrics = best_binary_threshold(
            [(float(sample["notional"]), bool(sample["label"])) for sample in samples]
        )
        imbalance_threshold, imbalance_metrics = best_binary_threshold(
            [(float(sample["imbalance"]), bool(sample["label"])) for sample in samples]
        )
        positive_moves = sorted(float(sample["move"]) for sample in positives)
        thresholds[format_window_label(window)] = {
            "window_seconds": window,
            "label_source": cfg.label_source,
            "sample_count": len(samples),
            "positive_count": len(positives),
            "pre_shock_move_pct": move_threshold * max(cfg.pre_shock_scale, 0.0),
            "shock_move_pct": move_threshold,
            "extreme_move_pct": max(quantile(positive_moves, cfg.extreme_quantile), move_threshold),
            "notional_threshold": notional_threshold,
            "imbalance_threshold": imbalance_threshold,
            "precision": move_metrics["precision"],
            "recall": move_metrics["recall"],
            "f1": move_metrics["f1"],
            "notional_f1": notional_metrics["f1"],
            "imbalance_f1": imbalance_metrics["f1"],
        }

    return {
        "method": "supervised_kline_threshold_learning",
        "label_source": cfg.label_source,
        "sample_count": sample_count,
        "positive_count": positive_count,
        "config": {
            "windows": list(cfg.windows),
            "positive_stage": cfg.positive_stage,
            "pre_shock_scale": cfg.pre_shock_scale,
            "extreme_quantile": cfg.extreme_quantile,
            "min_positive_count": cfg.min_positive_count,
        },
        "thresholds": thresholds,
    }


def supervised_window_samples(
    rows: Iterable[dict[str, object]],
    window: float,
    config: SupervisedThresholdConfig,
) -> list[dict[str, object]]:
    samples: list[dict[str, object]] = []
    for row in rows:
        sample = supervised_window_sample(row, window, config)
        if sample is not None:
            samples.append(sample)
    return samples


def supervised_window_sample(
    row: dict[str, object],
    window: float,
    config: SupervisedThresholdConfig,
) -> dict[str, object] | None:
    suffix = format_window_label(window).replace(".", "_")
    move = numeric_float(row.get(f"trade_move_pct_{suffix}"))
    notional = numeric_float(row.get(f"trade_notional_{suffix}"))
    imbalance = numeric_float(row.get(f"trade_imbalance_{suffix}"))
    if move is None or notional is None or imbalance is None:
        return None
    label = is_positive_kline_label(row, config)
    if is_opposite_kline_target(row, config):
        return None
    direction = row.get("kline_direction") or row.get("slow_wick_direction")
    if direction == "down_flush":
        directional_move = max(-move, 0.0)
        directional_imbalance = max(-imbalance, 0.0)
    elif direction == "up_squeeze":
        directional_move = max(move, 0.0)
        directional_imbalance = max(imbalance, 0.0)
    else:
        directional_move = abs(move)
        directional_imbalance = abs(imbalance)
    return {
        "label": label,
        "move": directional_move,
        "notional": max(notional, 0.0),
        "imbalance": directional_imbalance,
    }


def is_positive_kline_label(row: dict[str, object], config: SupervisedThresholdConfig) -> bool:
    if row.get("kline_stage") == config.positive_stage:
        return True
    if config.positive_stage == "slow_rebound_confirmed":
        return row.get("slow_wick_stage") == config.positive_stage or row.get("slow_wick_rebound_confirmed") is True
    return False


def is_opposite_kline_target(row: dict[str, object], config: SupervisedThresholdConfig) -> bool:
    stage = row.get("kline_stage")
    if config.positive_stage == "down_wick":
        return stage == "up_wick"
    if config.positive_stage == "up_wick":
        return stage == "down_wick"
    return False


def best_binary_threshold(samples: list[tuple[float, bool]]) -> tuple[float, dict[str, float]]:
    if not samples:
        return 0.0, {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    candidates = sorted({value for value, _label in samples if value > 0.0})
    if not candidates:
        return 0.0, {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    best_threshold = candidates[0]
    best_metrics = score_threshold(samples, best_threshold)
    for threshold in candidates[1:]:
        metrics = score_threshold(samples, threshold)
        if (
            metrics["f1"],
            metrics["precision"],
            metrics["recall"],
            -threshold,
        ) > (
            best_metrics["f1"],
            best_metrics["precision"],
            best_metrics["recall"],
            -best_threshold,
        ):
            best_threshold = threshold
            best_metrics = metrics
    return best_threshold, best_metrics


def score_threshold(samples: list[tuple[float, bool]], threshold: float) -> dict[str, float]:
    true_positive = false_positive = false_negative = 0
    for value, label in samples:
        predicted = value >= threshold
        if predicted and label:
            true_positive += 1
        elif predicted and not label:
            false_positive += 1
        elif not predicted and label:
            false_negative += 1
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def apply_learned_thresholds(config: PinStageConfig, learned: dict[str, object]) -> PinStageConfig:
    thresholds = learned.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("learned threshold JSON must contain a thresholds object")

    pre: list[tuple[float, float]] = []
    shock: list[tuple[float, float]] = []
    extreme: list[tuple[float, float]] = []
    notionals: list[tuple[float, float]] = []
    imbalances: list[float] = []

    for raw in thresholds.values():
        if not isinstance(raw, dict):
            continue
        window = positive_float(raw.get("window_seconds"))
        if window is None:
            continue
        add_window_value(pre, window, raw.get("pre_shock_move_pct"))
        add_window_value(shock, window, raw.get("shock_move_pct"))
        add_window_value(extreme, window, raw.get("extreme_move_pct"))
        add_window_value(notionals, window, raw.get("notional_threshold"))
        imbalance = positive_float(raw.get("imbalance_threshold"))
        if imbalance is not None:
            imbalances.append(imbalance)

    updates: dict[str, object] = {}
    if pre:
        updates["pre_shock_move_pct_by_window"] = tuple(sorted(pre))
        updates["pre_shock_move_pct"] = min(value for _window, value in pre)
    if shock:
        updates["shock_move_pct_by_window"] = tuple(sorted(shock))
        updates["shock_move_pct"] = min(value for _window, value in shock)
    if extreme:
        updates["extreme_move_pct_by_window"] = tuple(sorted(extreme))
        updates["extreme_move_pct"] = min(value for _window, value in extreme)
    if notionals:
        updates["min_trade_notional_by_window"] = tuple(sorted(notionals))
        updates["min_trade_notional"] = min(value for _window, value in notionals)
    if imbalances:
        updates["high_imbalance"] = sum(imbalances) / len(imbalances)

    return replace(config, **updates) if updates else config


def score_learned_candidate_signal(
    row: dict[str, object],
    learned: dict[str, object],
    *,
    direction: str | None,
    min_score: float = 1.0,
) -> CandidateSignalScore:
    thresholds = learned.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("learned threshold JSON must contain a thresholds object")
    target_stage = learned_target_stage(learned)
    best: CandidateSignalScore | None = None
    for raw in thresholds.values():
        if not isinstance(raw, dict):
            continue
        window = positive_float(raw.get("window_seconds"))
        if window is None:
            continue
        sample = learned_threshold_sample(row, window, direction)
        if sample is None:
            continue
        move_threshold = positive_float(raw.get("shock_move_pct")) or positive_float(raw.get("pre_shock_move_pct"))
        notional_threshold = positive_float(raw.get("notional_threshold"))
        imbalance_threshold = positive_float(raw.get("imbalance_threshold"))
        move_ratio = ratio(sample["move"], move_threshold)
        notional_ratio = ratio(sample["notional"], notional_threshold)
        imbalance_ratio = ratio(sample["imbalance"], imbalance_threshold)
        score = (
            0.55 * min(move_ratio, 2.0)
            + 0.25 * min(notional_ratio, 2.0)
            + 0.20 * min(imbalance_ratio, 2.0)
        )
        candidate = CandidateSignalScore(
            score=score,
            passed=score >= min_score,
            best_window_seconds=window,
            target_stage=target_stage,
            move_ratio=move_ratio,
            notional_ratio=notional_ratio,
            imbalance_ratio=imbalance_ratio,
        )
        if best is None or candidate.score > best.score:
            best = candidate
    if best is None:
        return CandidateSignalScore(
            score=0.0,
            passed=False,
            best_window_seconds=None,
            target_stage=target_stage,
            move_ratio=0.0,
            notional_ratio=0.0,
            imbalance_ratio=0.0,
        )
    return replace(best, passed=best.score >= min_score)


def learned_target_stage(learned: dict[str, object]) -> str | None:
    config = learned.get("config")
    if not isinstance(config, dict):
        return None
    stage = config.get("positive_stage")
    return str(stage) if stage else None


def learned_threshold_sample(
    row: dict[str, object],
    window: float,
    direction: str | None,
) -> dict[str, float] | None:
    suffix = format_window_label(window).replace(".", "_")
    move = numeric_float(row.get(f"trade_move_pct_{suffix}"))
    notional = numeric_float(row.get(f"trade_notional_{suffix}"))
    imbalance = numeric_float(row.get(f"trade_imbalance_{suffix}"))
    if move is None or notional is None or imbalance is None:
        return None
    if direction == "down_flush":
        directional_move = max(-move, 0.0)
        directional_imbalance = max(-imbalance, 0.0)
    elif direction == "up_squeeze":
        directional_move = max(move, 0.0)
        directional_imbalance = max(imbalance, 0.0)
    else:
        directional_move = abs(move)
        directional_imbalance = abs(imbalance)
    return {
        "move": directional_move,
        "notional": max(notional, 0.0),
        "imbalance": directional_imbalance,
    }


def ratio(value: float, threshold: float | None) -> float:
    if threshold is None or threshold <= 0.0:
        return 0.0
    return max(value, 0.0) / threshold


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    q = max(0.0, min(q, 1.0))
    position = (len(values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def add_window_value(target: list[tuple[float, float]], window: float, value: object) -> None:
    number = positive_float(value)
    if number is not None:
        target.append((window, number))


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


def learn_thresholds_from_trade_files(
    paths: Iterable[str | Path],
    *,
    symbol: str,
    windows: Iterable[float],
    keep_per_window: int,
    min_abs_move_pct: float,
    config: LearnedThresholdConfig,
) -> dict[str, object]:
    candidates: list[ShockCandidate] = []
    for path in paths:
        candidates.extend(
            scan_trade_file(
                path,
                symbol=symbol,
                windows=windows,
                keep_per_window=keep_per_window,
                min_abs_move_pct=min_abs_move_pct,
            )
        )
    return learn_thresholds_from_candidates(candidates, config)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Learn BTCUSDT pin-stage thresholds from historical trade distributions.")
    parser.add_argument("--trade", action="append", help="Bybit publicTrade CSV.gz path. Repeat for multiple days.")
    parser.add_argument("--labels-jsonl", help="Replay labels JSONL annotated by 1m kline, used for supervised threshold learning.")
    parser.add_argument("--target-stage", default="down_wick", help="Supervised kline target stage, e.g. down_wick, up_wick, trend_break.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--windows", default="5,15,30,45,60,120,180,240")
    parser.add_argument("--keep-per-window", type=int, default=5000)
    parser.add_argument("--min-abs-move-pct", type=float, default=0.0)
    parser.add_argument("--pre-quantile", type=float, default=0.95)
    parser.add_argument("--shock-quantile", type=float, default=0.99)
    parser.add_argument("--extreme-quantile", type=float, default=0.999)
    parser.add_argument("--notional-quantile", type=float, default=0.95)
    parser.add_argument("--imbalance-quantile", type=float, default=0.95)
    parser.add_argument("--min-candidates-per-window", type=int, default=3)
    parser.add_argument(
        "--negative-stride",
        type=int,
        default=20,
        help="For supervised labels, keep all positive rows and one out of N negative rows.",
    )
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    windows = parse_windows_arg(args.windows)
    if args.labels_jsonl:
        learned = learn_thresholds_from_labeled_rows(
            iter_labeled_feature_rows_mmap(args.labels_jsonl, windows, negative_stride=args.negative_stride),
            SupervisedThresholdConfig(
                windows=windows,
                positive_stage=args.target_stage,
                extreme_quantile=args.extreme_quantile,
            ),
        )
    else:
        if not args.trade:
            raise SystemExit("--trade is required when --labels-jsonl is not provided")
        learned = learn_thresholds_from_trade_files(
            args.trade,
            symbol=args.symbol,
            windows=windows,
            keep_per_window=args.keep_per_window,
            min_abs_move_pct=args.min_abs_move_pct,
            config=LearnedThresholdConfig(
                pre_quantile=args.pre_quantile,
                shock_quantile=args.shock_quantile,
                extreme_quantile=args.extreme_quantile,
                notional_quantile=args.notional_quantile,
                imbalance_quantile=args.imbalance_quantile,
                min_candidates_per_window=args.min_candidates_per_window,
            ),
        )
    Path(args.output).write_text(json.dumps(learned, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"thresholds: {args.output}")
    if "candidate_count" in learned:
        print(f"candidate_count: {learned['candidate_count']}")
    else:
        print(f"sample_count: {learned.get('sample_count', 0)}")
        print(f"positive_count: {learned.get('positive_count', 0)}")
    return 0


def iter_jsonl(path: str | Path) -> Iterable[dict[str, object]]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def iter_jsonl_mmap(path: str | Path) -> Iterable[dict[str, object]]:
    with open(path, "rb") as handle:
        if handle.seek(0, 2) == 0:
            return
        handle.seek(0)
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            while line := mapped.readline():
                if line.strip():
                    yield json.loads(line)


def iter_labeled_feature_rows_mmap(
    path: str | Path,
    windows: Iterable[float],
    *,
    negative_stride: int = 1,
) -> Iterable[dict[str, object]]:
    stride = max(int(negative_stride), 1)
    with open(path, "rb") as handle:
        if handle.seek(0, 2) == 0:
            return
        handle.seek(0)
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
            negative_index = 0
            while line := mapped.readline():
                if not line.strip():
                    continue
                feature_line = line
                trade_windows_start = line.find(b'"trade_windows"')
                if trade_windows_start >= 0:
                    feature_line = line[:trade_windows_start]
                row: dict[str, object] = {}
                kline_stage = extract_json_string(feature_line, "kline_stage")
                kline_direction = extract_json_string(feature_line, "kline_direction")
                stage = extract_json_string(feature_line, "slow_wick_stage")
                direction = extract_json_string(feature_line, "slow_wick_direction")
                rebound_confirmed = extract_json_bool(feature_line, "slow_wick_rebound_confirmed")
                is_positive = (
                    kline_stage in {"down_wick", "up_wick", "trend_break"}
                    or stage == "slow_rebound_confirmed"
                    or rebound_confirmed is True
                )
                if not is_positive:
                    negative_index += 1
                    if negative_index % stride != 0:
                        continue
                if kline_stage is not None:
                    row["kline_stage"] = kline_stage
                if kline_direction is not None:
                    row["kline_direction"] = kline_direction
                if stage is not None:
                    row["slow_wick_stage"] = stage
                if direction is not None:
                    row["slow_wick_direction"] = direction
                if rebound_confirmed is not None:
                    row["slow_wick_rebound_confirmed"] = rebound_confirmed
                for window in windows:
                    suffix = format_window_label(window).replace(".", "_")
                    for prefix in ("trade_move_pct", "trade_notional", "trade_imbalance"):
                        key = f"{prefix}_{suffix}"
                        value = extract_json_number(feature_line, key)
                        if value is not None:
                            row[key] = value
                yield row


def extract_json_string(line: bytes, key: str) -> str | None:
    start = value_start(line, key)
    if start is None or start >= len(line) or line[start] != ord('"'):
        return None
    end = line.find(b'"', start + 1)
    if end < 0:
        return None
    return line[start + 1 : end].decode("utf-8")


def extract_json_bool(line: bytes, key: str) -> bool | None:
    start = value_start(line, key)
    if start is None:
        return None
    if line.startswith(b"true", start):
        return True
    if line.startswith(b"false", start):
        return False
    return None


def extract_json_number(line: bytes, key: str) -> float | None:
    start = value_start(line, key)
    if start is None:
        return None
    end = start
    valid = b"+-.0123456789eE"
    while end < len(line) and line[end] in valid:
        end += 1
    if end == start:
        return None
    try:
        return float(line[start:end])
    except ValueError:
        return None


def value_start(line: bytes, key: str) -> int | None:
    needle = b'"' + key.encode("utf-8") + b'"'
    key_start = line.find(needle)
    if key_start < 0:
        return None
    colon = line.find(b":", key_start + len(needle))
    if colon < 0:
        return None
    start = colon + 1
    while start < len(line) and line[start] in b" \t\r\n":
        start += 1
    return start


if __name__ == "__main__":
    raise SystemExit(main())
