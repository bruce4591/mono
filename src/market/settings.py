from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_level: str


def load_settings() -> Settings:
    db_path = Path(os.environ.get("MARKET_DB_PATH", "./data/market.sqlite3"))
    log_level = os.environ.get("MARKET_LOG_LEVEL", "INFO")
    return Settings(db_path=db_path, log_level=log_level)

