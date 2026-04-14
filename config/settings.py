"""Trading Bot Configuration — Vault-Ready Credential Management.

Credential access abstracted through get_exchange_credentials() async function.
Currently reads from .env (simulates vault). Ready for HashiCorp Vault integration.

All paths resolved from PROJECT_ROOT via pathlib (Windows ↔ Linux).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Project root = directory containing run_bot.py
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

logger = logging.getLogger(__name__)


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


# ──────────────────────────────────────────────────────────────
# Vault-Ready Credential Provider
# ──────────────────────────────────────────────────────────────

class CredentialProvider:
    """Abstraction layer for credential retrieval.

    Current implementation: reads from environment variables (.env).
    Future: connect to HashiCorp Vault, AWS Secrets Manager, etc.

    Usage:
        creds = await credential_provider.get_exchange_credentials()
        api_key = creds["api_key"]
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._cache_ttl: float = 300.0  # 5 min cache
        self._last_fetch: float = 0.0

    async def get_exchange_credentials(self) -> dict[str, str]:
        """Retrieve exchange API credentials dynamically.

        Current: Reads from .env
        Future: HashiCorp Vault → vault_client.read("secret/data/exchange")
        """
        # ── Future Vault Implementation ──
        # import hvac
        # client = hvac.Client(url=os.getenv("VAULT_ADDR"))
        # client.token = os.getenv("VAULT_TOKEN")
        # secret = client.secrets.kv.v2.read_secret_version(
        #     path="exchange", mount_point="secret"
        # )
        # return {
        #     "api_key": secret["data"]["data"]["api_key"],
        #     "secret": secret["data"]["data"]["secret"],
        # }

        # Current: .env simulation of dynamic retrieval
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._read_from_env)

    def _read_from_env(self) -> dict[str, str]:
        """Synchronous .env read (simulates vault call latency)."""
        return {
            "api_key": os.getenv("EXCHANGE_API_KEY", ""),
            "secret": os.getenv("EXCHANGE_SECRET", ""),
            "exchange_id": os.getenv("EXCHANGE_ID", "binance"),
        }

    async def get_llm_credentials(self) -> dict[str, str]:
        """Retrieve LLM API keys dynamically."""
        loop = asyncio.get_running_loop()

        def _read() -> dict[str, str]:
            return {
                "gemini_key": os.getenv("GEMINI_API_KEY", ""),
                "claude_key": os.getenv("CLAUDE_API_KEY", ""),
            }

        return await loop.run_in_executor(None, _read)


# Singleton
credential_provider = CredentialProvider()


# ──────────────────────────────────────────────────────────────
# Settings Dataclasses
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExchangeSettings:
    """Exchange config — credentials loaded dynamically via CredentialProvider."""
    id: str = field(default_factory=lambda: _env("EXCHANGE_ID", "binance"))
    sandbox: bool = field(default_factory=lambda: _env_bool("EXCHANGE_SANDBOX", True))
    market_type: str = field(default_factory=lambda: _env("MARKET_TYPE", "spot"))  # spot | future | swap

    # Credentials are NOT stored here anymore.
    # Use: creds = await credential_provider.get_exchange_credentials()

    @property
    def api_key(self) -> str:
        """Legacy sync access — reads from env. Prefer async get_exchange_credentials()."""
        return _env("EXCHANGE_API_KEY")

    @property
    def secret(self) -> str:
        """Legacy sync access — reads from env. Prefer async get_exchange_credentials()."""
        return _env("EXCHANGE_SECRET")


@dataclass(frozen=True)
class TradingSettings:
    symbol: str = field(default_factory=lambda: _env("TRADING_SYMBOL", "BTC/USDT"))
    timeframe: str = field(default_factory=lambda: _env("TRADING_TIMEFRAME", "5m"))
    interval_seconds: int = field(default_factory=lambda: _env_int("TRADING_INTERVAL_SECONDS", 10))
    paper_trading: bool = field(default_factory=lambda: _env_bool("PAPER_TRADING_MODE", True))
    paper_initial_balance: float = field(default_factory=lambda: _env_float("PAPER_TRADING_INITIAL_BALANCE", 1000.0))
    max_position_pct: float = field(default_factory=lambda: _env_float("MAX_POSITION_PCT", 0.02))
    stop_loss_pct: float = field(default_factory=lambda: _env_float("STOP_LOSS_PCT", 0.03))
    max_open_trades: int = field(default_factory=lambda: _env_int("MAX_OPEN_TRADES", 3))
    cooldown_seconds: int = field(default_factory=lambda: _env_int("TRADE_COOLDOWN_SECONDS", 60))
    # Futures-specific
    leverage: int = field(default_factory=lambda: _env_int("FUTURES_LEVERAGE", 1))
    margin_mode: str = field(default_factory=lambda: _env("FUTURES_MARGIN_MODE", "isolated"))  # isolated | cross
    # Drawdown protection
    max_daily_drawdown_pct: float = field(default_factory=lambda: _env_float("MAX_DAILY_DRAWDOWN_PCT", 0.02))
    max_total_drawdown_pct: float = field(default_factory=lambda: _env_float("MAX_TOTAL_DRAWDOWN_PCT", 0.05))
    # Loss-rate limiter
    consecutive_loss_limit: int = field(default_factory=lambda: _env_int("CONSECUTIVE_LOSS_LIMIT", 3))
    loss_cooldown_seconds: int = field(default_factory=lambda: _env_int("LOSS_COOLDOWN_SECONDS", 900))


@dataclass(frozen=True)
class SentimentSettings:
    """Cerebro 3 configuration."""
    polling_seconds: int = field(default_factory=lambda: _env_int("SENTIMENT_POLLING_SECONDS", 300))
    panic_threshold: float = field(default_factory=lambda: _env_float("SENTIMENT_PANIC_THRESHOLD", -0.5))
    llm_provider: str = field(default_factory=lambda: _env("LLM_SENTIMENT_PROVIDER", "gemini"))
    enabled: bool = field(default_factory=lambda: _env_bool("SENTIMENT_ENABLED", True))


@dataclass(frozen=True)
class GeminiSettings:
    """Gemini dual-model routing — Flash (standard) + Pro (deep analysis)."""
    api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", ""))
    standard_model: str = field(default_factory=lambda: _env("GEMINI_STANDARD_MODEL", "gemini-2.5-flash"))
    pro_model: str = field(default_factory=lambda: _env("GEMINI_PRO_MODEL", "gemini-2.5-pro"))
    pro_obi_threshold: float = field(default_factory=lambda: _env_float("GEMINI_PRO_OBI_THRESHOLD", 0.80))
    pro_spread_multiplier: float = field(default_factory=lambda: _env_float("GEMINI_PRO_SPREAD_MULTIPLIER", 3.0))
    pro_cooldown_seconds: int = field(default_factory=lambda: _env_int("GEMINI_PRO_COOLDOWN_SECONDS", 300))
    pro_timeout_seconds: float = field(default_factory=lambda: _env_float("GEMINI_PRO_TIMEOUT_SECONDS", 4.0))


@dataclass(frozen=True)
class DashboardSettings:
    port: int = field(default_factory=lambda: _env_int("DASHBOARD_PORT", 8888))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET", "CHANGE_ME"))
    api_key: str = field(default_factory=lambda: _env("API_KEY", "CHANGE_ME"))
    allow_demo: bool = field(default_factory=lambda: _env_bool("ALLOW_DEMO_MODE", False))


@dataclass(frozen=True)
class TelegramSettings:
    """Optional Telegram push alerts."""
    enabled: bool = field(default_factory=lambda: _env_bool("TELEGRAM_ENABLED", False))
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID", ""))


@dataclass(frozen=True)
class PerformanceSettings:
    """Runtime performance toggles."""
    use_fast_loop: bool = field(default_factory=lambda: _env_bool("USE_FAST_LOOP", True))
    use_numba: bool = field(default_factory=lambda: _env_bool("USE_NUMBA", True))
    use_langgraph: bool = field(default_factory=lambda: _env_bool("USE_LANGGRAPH", False))
    use_orjson: bool = field(default_factory=lambda: _env_bool("USE_ORJSON", True))


@dataclass(frozen=True)
class RiskSettings:
    """Advanced risk controls — drawdown, loss streaks, trailing stops."""
    max_daily_loss_pct: float = field(default_factory=lambda: _env_float("MAX_DAILY_LOSS_PCT", 0.02))
    max_drawdown_pct: float = field(default_factory=lambda: _env_float("MAX_DRAWDOWN_PCT", 0.05))
    max_consecutive_losses: int = field(default_factory=lambda: _env_int("MAX_CONSECUTIVE_LOSSES", 3))
    loss_streak_cooldown_sec: int = field(default_factory=lambda: _env_int("LOSS_STREAK_COOLDOWN_SEC", 900))
    trailing_stop_pct: float = field(default_factory=lambda: _env_float("TRAILING_STOP_PCT", 0.015))
    paper_fee_pct: float = field(default_factory=lambda: _env_float("PAPER_FEE_PCT", 0.00075))
    fee_maker: float = field(default_factory=lambda: _env_float("TRADING_FEE_MAKER", 0.0002))
    fee_taker: float = field(default_factory=lambda: _env_float("TRADING_FEE_TAKER", 0.0004))
    paper_slippage_bps: float = field(default_factory=lambda: _env_float("PAPER_SLIPPAGE_BPS", 2.0))


@dataclass(frozen=True)
class StocksSettings:
    """Stocks domain configuration — broker provider + credentials."""
    provider: str = field(default_factory=lambda: _env("STOCKS_PROVIDER", "paper"))  # "paper" | "alpaca"
    alpaca_base_url: str = field(default_factory=lambda: _env("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"))
    alpaca_api_key: str = field(default_factory=lambda: _env("ALPACA_API_KEY", ""))
    alpaca_api_secret: str = field(default_factory=lambda: _env("ALPACA_API_SECRET", ""))
    max_consecutive_errors: int = field(default_factory=lambda: _env_int("STOCKS_MAX_CONSECUTIVE_ERRORS", 8))
    market_timezone: str = field(default_factory=lambda: _env("MARKET_TIMEZONE", "America/New_York"))


@dataclass(frozen=True)
class SupabaseSettings:
    """Optional Supabase integration for remote telemetry."""
    enabled: bool = field(default_factory=lambda: _env_bool("SUPABASE_ENABLED", False))
    url: str = field(default_factory=lambda: _env("SUPABASE_URL", ""))
    service_key: str = field(default_factory=lambda: _env("SUPABASE_SERVICE_KEY", ""))


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
    sentiment: SentimentSettings = field(default_factory=SentimentSettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)
    risk: RiskSettings = field(default_factory=RiskSettings)
    stocks: StocksSettings = field(default_factory=StocksSettings)
    supabase: SupabaseSettings = field(default_factory=SupabaseSettings)
    gemini: GeminiSettings = field(default_factory=GeminiSettings)
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    _log_file_raw: str = field(default_factory=lambda: _env("LOG_FILE", "data/bot.log"))

    @property
    def log_file(self) -> Path:
        return _resolve_path(self._log_file_raw)

    @property
    def project_root(self) -> Path:
        return _PROJECT_ROOT

    def validate(self) -> None:
        """Raise if critical settings are missing or insecure."""
        if not self.exchange.api_key:
            raise ValueError("EXCHANGE_API_KEY is required in .env")
        if not self.exchange.secret:
            raise ValueError("EXCHANGE_SECRET is required in .env")

        # --- Security: hard-fail on insecure secrets ---
        _WEAK = {"", "CHANGE_ME", "CHANGE_ME_TO_RANDOM_STRING"}
        if self.dashboard.jwt_secret in _WEAK or len(self.dashboard.jwt_secret) < 16:
            raise RuntimeError(
                "JWT_SECRET is unsafe (default/empty/too short). "
                "Set a strong secret (>=16 chars) in .env before starting the bot."
            )
        if self.dashboard.api_key in _WEAK or len(self.dashboard.api_key) < 8:
            raise RuntimeError(
                "API_KEY is unsafe (default/empty/too short). "
                "Set a strong dashboard key (>=8 chars) in .env before starting the bot."
            )

        # --- Stocks: validate broker credentials when not paper ---
        if self.stocks.provider == "alpaca":
            if not self.stocks.alpaca_api_key or len(self.stocks.alpaca_api_key) < 8:
                raise RuntimeError(
                    "ALPACA_API_KEY is missing or too short. "
                    "Set valid Alpaca credentials in .env when STOCKS_PROVIDER=alpaca."
                )
            if not self.stocks.alpaca_api_secret or len(self.stocks.alpaca_api_secret) < 8:
                raise RuntimeError(
                    "ALPACA_API_SECRET is missing or too short. "
                    "Set valid Alpaca credentials in .env when STOCKS_PROVIDER=alpaca."
                )

        # --- Supabase: validate credentials when enabled ---
        if self.supabase.enabled:
            if not self.supabase.url:
                raise RuntimeError(
                    "SUPABASE_URL is required when SUPABASE_ENABLED=true. "
                    "Set the project URL in .env."
                )
            if not self.supabase.service_key:
                raise RuntimeError(
                    "SUPABASE_SERVICE_KEY is required when SUPABASE_ENABLED=true. "
                    "Set the service/anon key in .env."
                )

        # --- Gemini: warn if sentiment is enabled but no API key ---
        if self.sentiment.enabled and not self.gemini.api_key:
            logger.warning(
                "GEMINI_API_KEY not set — Oracle will run in keyword-only mode"
            )


# Singleton
settings = Settings()

