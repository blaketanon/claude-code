"""Central configuration (12-factor, env-driven)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # core
    env: Literal["dev", "test", "prod"] = Field("dev", alias="ALPHAFORGE_ENV")
    data_dir: Path = Field(Path("./data"), alias="ALPHAFORGE_DATA_DIR")
    db_url: str = Field("sqlite:///./data/alphaforge.db", alias="ALPHAFORGE_DB_URL")
    secret_key: str = Field("dev-secret-change-me", alias="ALPHAFORGE_SECRET_KEY")
    admin_user: str = Field("admin", alias="ALPHAFORGE_ADMIN_USER")
    admin_password: str = Field("admin", alias="ALPHAFORGE_ADMIN_PASSWORD")
    access_token_minutes: int = 12 * 60

    # trading
    trading_mode: Literal["paper", "live"] = Field("paper", alias="ALPHAFORGE_TRADING_MODE")
    live_trading_ack: str = Field("", alias="ALPHAFORGE_LIVE_TRADING_ACK")
    symbols: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"], alias="ALPHAFORGE_SYMBOLS"
    )
    timeframe: str = Field("5m", alias="ALPHAFORGE_TIMEFRAME")
    data_provider: Literal["synthetic", "yfinance", "alpaca"] = Field(
        "synthetic", alias="ALPHAFORGE_DATA_PROVIDER"
    )

    # broker
    alpaca_api_key: str = Field("", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field("", alias="ALPACA_API_SECRET")
    alpaca_paper: bool = Field(True, alias="ALPACA_PAPER")

    # llm
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")
    llm_model: str = Field("claude-opus-5-5", alias="ALPHAFORGE_LLM_MODEL")

    # risk defaults (overridable at runtime via admin API)
    initial_capital: float = 100_000.0
    max_position_pct: float = 0.20          # max 20% of equity in a single name
    max_gross_exposure: float = 1.0         # 100% gross (no leverage by default)
    daily_loss_limit_pct: float = 0.02      # halt for the day after -2%
    max_drawdown_kill_pct: float = 0.10     # hard kill switch at -10% from peak
    target_annual_vol: float = 0.15         # volatility targeting
    kelly_fraction: float = 0.25            # fractional Kelly cap
    min_signal_confidence: float = 0.55     # below this we stay flat
    eod_flatten_minutes_before_close: int = 10
    pdt_guard: bool = True                  # respect pattern-day-trader rule when equity < 25k

    @field_validator("symbols", mode="before")
    @classmethod
    def _split_symbols(cls, v):
        if isinstance(v, str):
            return [s.strip().upper() for s in v.split(",") if s.strip()]
        return v

    @property
    def live_enabled(self) -> bool:
        return self.trading_mode == "live" and self.live_trading_ack == "I_UNDERSTAND_THE_RISK"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def bars_dir(self) -> Path:
        return self.data_dir / "bars"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.models_dir, self.bars_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
