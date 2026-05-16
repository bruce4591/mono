from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Any

from market.realtime import parse_binance_futures_depth_event, parse_binance_futures_trade_event
from market.strategy.paper import PaperStrategyResult, evaluate_pin_paper_strategy_events
from market.trade.pin_label_backtest import (
    CandidateSignalScorer,
    KlineCurveCandidate,
    KlineCurveCandidateConfig,
    append_kline_curve_candidate_rows,
    apply_candidate_signal_scorer,
    kline_curve_candidates,
    row_timestamp,
)
from market.trade.pin_stage_replay import (
    OrderBook,
    PinStageConfig,
    PinStageDetector,
    Stage,
    StageEvent,
    TradeEvent,
    isoformat_seconds,
)


DEFAULT_PIN_STRATEGY_SYMBOLS = ("BTCUSDT", "ETHUSDT")


class RealtimePinPaperStrategyEngine:
    def __init__(
        self,
        *,
        symbols: list[str] | tuple[str, ...] = DEFAULT_PIN_STRATEGY_SYMBOLS,
        config: PinStageConfig | None = None,
        config_overrides: dict[str, object] | None = None,
        candidate_signal_scorer: CandidateSignalScorer | None = None,
        kline_curve_candidate_config: KlineCurveCandidateConfig | None = None,
    ) -> None:
        base_config = config or PinStageConfig()
        if config_overrides:
            base_config = replace(base_config, **config_overrides)
        self.symbols = {symbol.upper() for symbol in symbols}
        self.config = base_config
        self.candidate_signal_scorer = candidate_signal_scorer
        self.kline_curve_candidate_config = kline_curve_candidate_config
        self.detectors = {
            symbol: PinStageDetector(base_config)
            for symbol in self.symbols
        }
        self.books = {symbol: OrderBook() for symbol in self.symbols}
        self.book_timestamps: dict[str, float | None] = {
            symbol: None
            for symbol in self.symbols
        }
        self.kline_candles: dict[str, list[dict[str, float]]] = {
            symbol: []
            for symbol in self.symbols
        }
        self.kline_candidates: dict[str, list[KlineCurveCandidate]] = {
            symbol: []
            for symbol in self.symbols
        }

    def on_kline_bar(self, symbol: str, candle: dict[str, float]) -> None:
        normalized_symbol = symbol.upper()
        if normalized_symbol not in self.symbols:
            return
        if self.kline_curve_candidate_config is None:
            return
        candles = self.kline_candles[normalized_symbol]
        candles.append(candle)
        history_size = max(self.kline_curve_candidate_config.history_size, 1)
        max_window = max(self.kline_curve_candidate_config.windows_minutes or (1,))
        self.kline_candles[normalized_symbol] = candles[-(history_size + max_window + 4) :]
        self.kline_candidates[normalized_symbol] = kline_curve_candidates(
            self.kline_candles[normalized_symbol],
            self.kline_curve_candidate_config,
        )

    def on_depth(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        event = parse_binance_futures_depth_event(payload)
        if event.symbol not in self.symbols:
            return
        book = self.books[event.symbol]
        book.apply_snapshot(event.bids, event.asks)
        self.book_timestamps[event.symbol] = event.timestamp

    def on_trade(
        self,
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> list[PaperStrategyResult]:
        event = parse_binance_futures_trade_event(payload)
        if event.symbol not in self.symbols:
            return []
        detector = self.detectors[event.symbol]
        book = self.books[event.symbol].metrics()
        stage_event = detector.on_trade(
            TradeEvent(
                timestamp=event.timestamp,
                symbol=event.symbol,
                side=event.side,  # type: ignore[arg-type]
                size=event.size,
                price=event.price,
            ),
            book,
            book_timestamp=self.book_timestamps[event.symbol],
        )
        return self._apply_stage_event(connection, stage_event)

    def _apply_stage_event(
        self,
        connection: sqlite3.Connection,
        stage_event: StageEvent,
    ) -> list[PaperStrategyResult]:
        stage_payload = self._stage_payload(stage_event)
        payloads = [stage_payload]
        candidate_payload = self._kline_candidate_payload(stage_event.symbol, stage_payload)
        if candidate_payload is not None:
            payloads.append(candidate_payload)
        results: list[PaperStrategyResult] = []
        for payload in payloads:
            results.extend(
                evaluate_pin_paper_strategy_events(
                    connection,
                    market="CRYPTO_FUTURES",
                    symbol=stage_event.symbol,
                    stage_event=payload,
                )
            )
        return results

    def _stage_payload(self, stage_event: StageEvent) -> dict[str, object]:
        stage_payload = stage_event.to_label()
        stage_payload.update(
            {
                "timestamp": isoformat_seconds(stage_event.timestamp),
                "price": stage_event.entry_candidate_price or stage_event.price,
                "signal_timeframe": "realtime",
                "chart_period": "1m",
            }
        )
        if self.candidate_signal_scorer is not None:
            stage_payload = apply_candidate_signal_scorer(
                stage_payload,
                self.candidate_signal_scorer,
            )
        return stage_payload

    def _kline_candidate_payload(
        self,
        symbol: str,
        stage_payload: dict[str, object],
    ) -> dict[str, object] | None:
        if self.kline_curve_candidate_config is None:
            return None
        candidates = [
            candidate
            for candidate in self.kline_candidates.get(symbol.upper(), [])
            if candidate.ended_at <= row_timestamp(stage_payload)
        ]
        if not candidates:
            return None
        rows = append_kline_curve_candidate_rows(
            [stage_payload],
            candidates,
            config=self.config,
            scorer=self.candidate_signal_scorer,
            candidate_config=self.kline_curve_candidate_config,
        )
        candidate_rows = [
            row
            for row in rows
            if row.get("candidate_source") == "kline_curve"
        ]
        if not candidate_rows:
            return None
        candidate_row = candidate_rows[-1]
        candidate_row.update(
            {
                "signal_timeframe": "realtime",
                "chart_period": "1m",
            }
        )
        return candidate_row
