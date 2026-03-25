"""Trading Bot Configuration — Dynamic paths resolved from PROJECT_ROOT."""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Project root = directory containing run_bot.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


def _resolve_path(relative: str) -> Path:
    """Resolve a path relative to PROJECT_ROOT. Absolute paths pass through."""
    p = Path(relative)
    if p.is_absolute():
        return p
    return _PROJECT_ROOT / p


@dataclass(frozen=True)
class ExchangeSettings:
    id: str = field(default_factory=lambda: _env("EXCHANGE_ID", "binance"))
    api_key: str = field(default_factory=lambda: _env("EXCHANGE_API_KEY"))
    secret: str = field(default_factory=lambda: _env("EXCHANGE_SECRET"))
    sandbox: bool = field(default_factory=lambda: _env_bool("EXCHANGE_SANDBOX", True))


@dataclass(frozen=True)
class TradingSettings:
    symbol: str = field(default_factory=lambda: _env("TRADING_SYMBOL", "BTC/USDT"))
    timeframe: str = field(default_factory=lambda: _env("TRADING_TIMEFRAME", "5m"))
    interval_seconds: int = field(default_factory=lambda: _env_int("TRADING_INTERVAL_SECONDS", 10))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.02))
    stop_loss_pct: float = field(default_factory=lambda: _env_float("STOP_LOSS_PCT", 0.03))
    max_open_trades: int = field(default_factory=lambda: _env_int("MAX_OPEN_TRADES", 3))
    cooldown_seconds: int = field(default_factory=lambda: _env_int("TRADE_COOLDOWN_SECONDS", 60))


@dataclass(frozen=True)
class DashboardSettings:
    port: int = field(default_factory=lambda: _env_int("DASHBOARD_PORT", 8888))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "CHANGE_ME"))
    api_key: str = field(default_factory=lambda: _env("API_KEY", "CHANGE_ME"))


@dataclass(frozen=True)
class DatabaseSettings:
    """Paths are resolved relative to PROJECT_ROOT at access time."""
    _duckdb_raw: str = field(default_factory=lambda: _env("DUCKDB_PATH", "data/analytics.duckdb"))
    _sqlite_raw: str = field(default_factory=lambda: _env("SQLITE_PATH", "data/trades.db"))

    @property
    def duckdb_path(self) -> Path:
        return _resolve_path(self._duckdb_raw)

    @property
    def sqlite_path(self) -> Path:
        return _resolve_path(self._sqlite_raw)


@dataclass(frozen=True)
class Settings:
    exchange: ExchangeSettings = field(default_factory=ExchangeSettings)
    trading: TradingSettings = field(default_factory=TradingSettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    _log_file_raw: str = field(default_factory=lambda: _env("LOG_FILE", "data/bot.log"))

    @property
    def log_file(self) -> Path:
        return _resolve_path(self._log_file_raw)

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    def validate(self) -> None:
        """Raise ValueError if critical settings are missing."""
        if not self.exchange.api_key:
            raise ValueError("EXCHANGE_API_KEY is required in .env")
        if not self.exchange.secret:
            raise ValueError("EXCHANGE_SECRET is required in .env")
        if self.dashboard.jwt_secret == "CHANGE_ME":
            logging.warning("⚠️  JWT_SECRET is using default value — change it in .env!")
        if self.dashboard.api_key == "CHANGE_ME":
            logging.warning("⚠️  API_KEY is using default value — change it in .env!")


# Singleton
settings = Settings()
