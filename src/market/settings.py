from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    db_path: Path | None
    log_level: str


def load_settings() -> Settings:
    database_url = os.environ.get("MARKET_DATABASE_URL")
    legacy_db_path = os.environ.get("MARKET_DB_PATH")
    if database_url is None:
        db_path = Path(legacy_db_path or "data/market.sqlite3")
        database_url = f"sqlite:///{db_path}"
    else:
        db_path = None
        if database_url.startswith("sqlite:///"):
            db_path = Path(database_url.removeprefix("sqlite:///"))
    log_level = os.environ.get("MARKET_LOG_LEVEL", "INFO")
    return Settings(database_url=database_url, db_path=db_path, log_level=log_level)
