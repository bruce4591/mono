from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from market.binance_futures import binance_futures_symbol_to_instrument
from market.repositories import InstrumentRepository


PIN_STRATEGY_ID = "crypto_pin_rebound_v1"
DEFAULT_FIXED_ORDER_NOTIONAL = 3_000.0
DEFAULT_TAKE_PROFIT_PCT = 0.006
DEFAULT_FEE_RATE = 0.0006
DEFAULT_MAX_OPEN_POSITIONS = 30


@dataclass(frozen=True)
class PaperStrategyResult:
    strategy_id: str
    market: str
    symbol: str
    action: str
    price: float
    realized_return_pct: float | None
    message: str
    event_time_utc: str


def evaluate_pin_paper_strategy(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    stage_event: dict[str, object],
    strategy_id: str = PIN_STRATEGY_ID,
) -> PaperStrategyResult | None:
    results = evaluate_pin_paper_strategy_events(
        connection,
        market=market,
        symbol=symbol,
        stage_event=stage_event,
        strategy_id=strategy_id,
    )
    return results[0] if results else None


def evaluate_pin_paper_strategy_events(
    connection: sqlite3.Connection,
    *,
    market: str,
    symbol: str,
    stage_event: dict[str, object],
    strategy_id: str = PIN_STRATEGY_ID,
) -> list[PaperStrategyResult]:
    ensure_paper_strategy_schema(connection)
    _ensure_strategy_definition(connection, strategy_id)
    normalized_symbol = symbol.upper()
    instrument_id = _ensure_instrument(connection, market, normalized_symbol)
    stage = str(stage_event["stage"])
    direction = str(stage_event.get("direction") or "")
    price = float(stage_event["price"])
    event_time = str(stage_event["timestamp"])
    payload = _serialize_payload(connection, stage_event)
    results: list[PaperStrategyResult] = []

    results.extend(
        _close_profitable_positions(
            connection,
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            market=market,
            symbol=normalized_symbol,
            price=price,
            event_time=event_time,
            signal_payload=payload,
        )
    )

    if stage == "rebound_confirmed" and direction == "down_flush":
        if stage_event.get("candidate_signal_passed") is False:
            return results
        entries = _entry_orders_for_signal(stage_event, fallback_price=price)
        open_count = _open_position_count(connection, strategy_id, instrument_id)
        for entry_price, notional in entries:
            if open_count >= DEFAULT_MAX_OPEN_POSITIONS:
                break
            result = _open_long_position(
                connection,
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                market=market,
                symbol=normalized_symbol,
                price=entry_price,
                notional=notional,
                event_time=event_time,
                signal_payload=payload,
            )
            results.append(result)
            open_count += 1
        return results

    if stage == "rebound_confirmed" and direction == "up_squeeze":
        results.extend(
            _close_all_open_positions(
                connection,
                strategy_id=strategy_id,
                instrument_id=instrument_id,
                market=market,
                symbol=normalized_symbol,
                price=price,
                event_time=event_time,
                signal_payload=payload,
                reason="opposite_rebound_signal",
            )
        )
        return results

    if stage in {"trend_break"}:
        return results
    return results


def _open_long_position(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    instrument_id: int,
    market: str,
    symbol: str,
    price: float,
    notional: float,
    event_time: str,
    signal_payload: object,
) -> PaperStrategyResult:
    quantity = notional / price
    fee = notional * DEFAULT_FEE_RATE
    position_id = _insert_position(
        connection,
        strategy_id=strategy_id,
        instrument_id=instrument_id,
        market=market,
        symbol=symbol,
        side="long",
        entry_price=price,
        entry_notional=notional,
        quantity=quantity,
        entry_fee=fee,
        opened_at_utc=event_time,
        signal_payload=signal_payload,
    )
    _insert_trade(
        connection,
        strategy_id=strategy_id,
        position_id=position_id,
        instrument_id=instrument_id,
        action="open_long",
        price=price,
        notional=notional,
        quantity=quantity,
        fee=fee,
        event_time=event_time,
        realized_pnl=None,
        realized_return_pct=None,
        signal_payload=signal_payload,
    )
    message = f"{symbol} Pin 模拟开多 成交价 {price:g} 名义本金 {notional:g}"
    return PaperStrategyResult(
        strategy_id,
        market,
        symbol,
        "open_long",
        price,
        None,
        message,
        event_time,
    )


def _close_profitable_positions(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    instrument_id: int,
    market: str,
    symbol: str,
    price: float,
    event_time: str,
    signal_payload: object,
) -> list[PaperStrategyResult]:
    rows = _open_positions(connection, strategy_id, instrument_id)
    results: list[PaperStrategyResult] = []
    for row in rows:
        entry_price = float(row["entry_price"])
        if price >= entry_price * (1.0 + DEFAULT_TAKE_PROFIT_PCT):
            results.append(
                _close_position(
                    connection,
                    row,
                    strategy_id=strategy_id,
                    instrument_id=instrument_id,
                    market=market,
                    symbol=symbol,
                    price=price,
                    event_time=event_time,
                    signal_payload=signal_payload,
                    action="close_long",
                    reason="take_profit",
                )
            )
    return results


def _close_all_open_positions(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    instrument_id: int,
    market: str,
    symbol: str,
    price: float,
    event_time: str,
    signal_payload: object,
    reason: str,
) -> list[PaperStrategyResult]:
    return [
        _close_position(
            connection,
            row,
            strategy_id=strategy_id,
            instrument_id=instrument_id,
            market=market,
            symbol=symbol,
            price=price,
            event_time=event_time,
            signal_payload=signal_payload,
            action="close_long",
            reason=reason,
        )
        for row in _open_positions(connection, strategy_id, instrument_id)
    ]


def _close_position(
    connection: sqlite3.Connection,
    position: sqlite3.Row,
    *,
    strategy_id: str,
    instrument_id: int,
    market: str,
    symbol: str,
    price: float,
    event_time: str,
    signal_payload: object,
    action: str,
    reason: str,
) -> PaperStrategyResult:
    entry_price = float(position["entry_price"])
    quantity = _optional_float(position["quantity"]) or 0.0
    entry_notional = _optional_float(position["entry_notional"]) or entry_price * quantity
    entry_fee = _optional_float(position["entry_fee"]) or entry_notional * DEFAULT_FEE_RATE
    exit_notional = quantity * price
    exit_fee = exit_notional * DEFAULT_FEE_RATE
    realized_pnl = exit_notional - exit_fee - entry_notional - entry_fee
    realized_return_pct = (realized_pnl / entry_notional) * 100.0 if entry_notional > 0 else 0.0
    position_id = int(position["paper_position_id"])
    connection.execute(
        """
        UPDATE paper_position
        SET status = 'closed',
            exit_price = ?,
            exit_notional = ?,
            exit_fee = ?,
            closed_at_utc = ?,
            realized_pnl = ?,
            realized_return_pct = ?,
            updated_at_utc = ?
        WHERE paper_position_id = ?
        """,
        (
            price,
            exit_notional,
            exit_fee,
            event_time,
            realized_pnl,
            realized_return_pct,
            event_time,
            position_id,
        ),
    )
    _insert_trade(
        connection,
        strategy_id=strategy_id,
        position_id=position_id,
        instrument_id=instrument_id,
        action=action,
        price=price,
        notional=exit_notional,
        quantity=quantity,
        fee=exit_fee,
        event_time=event_time,
        realized_pnl=realized_pnl,
        realized_return_pct=realized_return_pct,
        signal_payload=signal_payload,
    )
    message = f"{symbol} Pin 模拟平多 成交价 {price:g} 收益率 {realized_return_pct:+.2f}% 原因 {reason}"
    return PaperStrategyResult(
        strategy_id,
        market,
        symbol,
        action,
        price,
        realized_return_pct,
        message,
        event_time,
    )


def ensure_paper_strategy_schema(connection: sqlite3.Connection) -> None:
    if getattr(connection, "backend", "sqlite") == "postgres":
        _ensure_postgres_paper_strategy_schema(connection)
        return
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_definition (
            strategy_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            execution_mode TEXT NOT NULL DEFAULT 'paper',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_position (
            paper_position_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            instrument_id INTEGER NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_notional REAL,
            quantity REAL,
            entry_fee REAL,
            exit_price REAL,
            exit_notional REAL,
            exit_fee REAL,
            opened_at_utc TEXT NOT NULL,
            closed_at_utc TEXT,
            realized_pnl REAL,
            realized_return_pct REAL,
            signal_payload TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        )
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_paper_position_open")
    _ensure_sqlite_columns(
        connection,
        "paper_position",
        {
            "entry_notional": "REAL",
            "quantity": "REAL",
            "entry_fee": "REAL",
            "exit_notional": "REAL",
            "exit_fee": "REAL",
            "realized_pnl": "REAL",
        },
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trade (
            paper_trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id TEXT NOT NULL,
            paper_position_id INTEGER NOT NULL,
            instrument_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            price REAL NOT NULL,
            notional REAL,
            quantity REAL,
            fee REAL,
            event_time_utc TEXT NOT NULL,
            realized_pnl REAL,
            realized_return_pct REAL,
            signal_payload TEXT NOT NULL DEFAULT '{}',
            created_at_utc TEXT NOT NULL
        )
        """
    )
    _ensure_sqlite_columns(
        connection,
        "paper_trade",
        {
            "notional": "REAL",
            "quantity": "REAL",
            "fee": "REAL",
            "realized_pnl": "REAL",
        },
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_trade_strategy_time
        ON paper_trade (strategy_id, event_time_utc DESC, paper_trade_id DESC)
        """
    )


def _ensure_postgres_paper_strategy_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS strategy_definition (
            strategy_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            execution_mode TEXT NOT NULL DEFAULT 'paper',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at_utc TIMESTAMPTZ NOT NULL,
            updated_at_utc TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_position (
            paper_position_id BIGSERIAL PRIMARY KEY,
            strategy_id TEXT NOT NULL REFERENCES strategy_definition(strategy_id),
            instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            entry_notional DOUBLE PRECISION,
            quantity DOUBLE PRECISION,
            entry_fee DOUBLE PRECISION,
            exit_price DOUBLE PRECISION,
            exit_notional DOUBLE PRECISION,
            exit_fee DOUBLE PRECISION,
            opened_at_utc TIMESTAMPTZ NOT NULL,
            closed_at_utc TIMESTAMPTZ,
            realized_pnl DOUBLE PRECISION,
            realized_return_pct DOUBLE PRECISION,
            signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at_utc TIMESTAMPTZ NOT NULL,
            updated_at_utc TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute("DROP INDEX IF EXISTS idx_paper_position_open")
    for column in (
        "entry_notional",
        "quantity",
        "entry_fee",
        "exit_notional",
        "exit_fee",
        "realized_pnl",
    ):
        connection.execute(
            f"ALTER TABLE paper_position ADD COLUMN IF NOT EXISTS {column} DOUBLE PRECISION"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_trade (
            paper_trade_id BIGSERIAL PRIMARY KEY,
            strategy_id TEXT NOT NULL REFERENCES strategy_definition(strategy_id),
            paper_position_id BIGINT NOT NULL REFERENCES paper_position(paper_position_id),
            instrument_id BIGINT NOT NULL REFERENCES instrument(instrument_id),
            action TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            notional DOUBLE PRECISION,
            quantity DOUBLE PRECISION,
            fee DOUBLE PRECISION,
            event_time_utc TIMESTAMPTZ NOT NULL,
            realized_pnl DOUBLE PRECISION,
            realized_return_pct DOUBLE PRECISION,
            signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at_utc TIMESTAMPTZ NOT NULL
        )
        """
    )
    for column in ("notional", "quantity", "fee", "realized_pnl"):
        connection.execute(
            f"ALTER TABLE paper_trade ADD COLUMN IF NOT EXISTS {column} DOUBLE PRECISION"
        )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_paper_trade_strategy_time
        ON paper_trade (strategy_id, event_time_utc DESC, paper_trade_id DESC)
        """
    )


def _ensure_strategy_definition(connection: sqlite3.Connection, strategy_id: str) -> None:
    connection.execute(
        """
        INSERT INTO strategy_definition (
            strategy_id, name, description, execution_mode, enabled, created_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, 'paper', TRUE, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(strategy_id) DO UPDATE SET
            updated_at_utc = CURRENT_TIMESTAMP
        """,
        (
            strategy_id,
            "Crypto Pin Rebound v1",
            "Paper strategy driven by realtime futures trade and order-book pin stage signals.",
        ),
    )


def _ensure_instrument(connection: sqlite3.Connection, market: str, symbol: str) -> int:
    if market == "CRYPTO_FUTURES":
        instrument = binance_futures_symbol_to_instrument({"symbol": symbol})
        return InstrumentRepository(connection).upsert(instrument)
    row = connection.execute(
        """
        SELECT instrument_id FROM instrument
        WHERE market = ? AND symbol = ? AND is_active = TRUE
        LIMIT 1
        """,
        (market, symbol),
    ).fetchone()
    if row is None:
        raise ValueError(f"instrument not found: {market} {symbol}")
    return int(row["instrument_id"])


def _open_position_count(
    connection: sqlite3.Connection,
    strategy_id: str,
    instrument_id: int,
) -> int:
    row = connection.execute(
        """
        SELECT count(*) AS count
        FROM paper_position
        WHERE strategy_id = ?
            AND instrument_id = ?
            AND status = 'open'
        """,
        (strategy_id, instrument_id),
    ).fetchone()
    return int(row["count"]) if row is not None else 0


def _open_positions(
    connection: sqlite3.Connection,
    strategy_id: str,
    instrument_id: int,
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT *
            FROM paper_position
            WHERE strategy_id = ?
                AND instrument_id = ?
                AND status = 'open'
            ORDER BY paper_position_id
            """,
            (strategy_id, instrument_id),
        ).fetchall()
    )


def _entry_orders_for_signal(
    stage_event: dict[str, object],
    *,
    fallback_price: float,
) -> list[tuple[float, float]]:
    raw_slices = stage_event.get("entry_order_slices")
    entries: list[tuple[float, float]] = []
    if isinstance(raw_slices, list):
        for index, raw in enumerate(raw_slices):
            if not isinstance(raw, dict):
                continue
            if index != 0 and raw.get("trigger") != "rebound_confirmed":
                continue
            price = _optional_float(raw.get("price"))
            notional = _optional_float(raw.get("notional"))
            if price is None or price <= 0 or notional is None or notional <= 0:
                continue
            entries.append((price, notional))
    if entries:
        return entries
    notional = _optional_float(stage_event.get("entry_order_notional")) or DEFAULT_FIXED_ORDER_NOTIONAL
    return [(fallback_price, notional)]


def _open_position(
    connection: sqlite3.Connection,
    strategy_id: str,
    instrument_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM paper_position
        WHERE strategy_id = ?
            AND instrument_id = ?
            AND status = 'open'
        ORDER BY paper_position_id DESC
        LIMIT 1
        """,
        (strategy_id, instrument_id),
    ).fetchone()


def _insert_position(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    instrument_id: int,
    market: str,
    symbol: str,
    side: str,
    entry_price: float,
    entry_notional: float,
    quantity: float,
    entry_fee: float,
    opened_at_utc: str,
    signal_payload: object,
) -> int:
    row = connection.execute(
        """
        INSERT INTO paper_position (
            strategy_id, instrument_id, market, symbol, side, status, entry_price,
            entry_notional, quantity, entry_fee, opened_at_utc, signal_payload,
            created_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING paper_position_id
        """,
        (
            strategy_id,
            instrument_id,
            market,
            symbol,
            side,
            entry_price,
            entry_notional,
            quantity,
            entry_fee,
            opened_at_utc,
            signal_payload,
            opened_at_utc,
            opened_at_utc,
        ),
    ).fetchone()
    if row is None:
        raise RuntimeError("paper position insert did not return an id")
    return int(row["paper_position_id"])


def _insert_trade(
    connection: sqlite3.Connection,
    *,
    strategy_id: str,
    position_id: int,
    instrument_id: int,
    action: str,
    price: float,
    notional: float,
    quantity: float,
    fee: float,
    event_time: str,
    realized_pnl: float | None,
    realized_return_pct: float | None,
    signal_payload: object,
) -> None:
    connection.execute(
        """
        INSERT INTO paper_trade (
            strategy_id, paper_position_id, instrument_id, action, price,
            notional, quantity, fee, event_time_utc, realized_pnl,
            realized_return_pct, signal_payload, created_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            strategy_id,
            position_id,
            instrument_id,
            action,
            price,
            notional,
            quantity,
            fee,
            event_time,
            realized_pnl,
            realized_return_pct,
            signal_payload,
            event_time,
        ),
    )


def _serialize_payload(connection: sqlite3.Connection, payload: dict[str, object]) -> object:
    if getattr(connection, "backend", "sqlite") == "postgres":
        try:
            from psycopg.types.json import Jsonb
        except ImportError:
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return Jsonb(payload)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_sqlite_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for column, definition in columns.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {definition}")
