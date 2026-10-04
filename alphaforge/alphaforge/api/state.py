"""Process-wide singletons shared by routers."""
from __future__ import annotations

from alphaforge.config import get_settings
from alphaforge.engine import EngineConfig, LiveEngine
from alphaforge.models import ModelRegistry
from alphaforge.risk import RiskLimits
from alphaforge.strategy import PolicyConfig
from alphaforge.metrics.evaluation import annualization
from .jobs import JobRunner


class AppState:
    def __init__(self):
        self.jobs = JobRunner()
        self.registry = ModelRegistry()
        self.engine: LiveEngine | None = None

    def get_engine(self) -> LiveEngine:
        if self.engine is None:
            s = get_settings()
            cfg = EngineConfig(
                symbols=s.symbols,
                timeframe=s.timeframe,
                policy=PolicyConfig(min_confidence=s.min_signal_confidence, max_weight=s.max_position_pct, gross_cap=s.max_gross_exposure, target_vol=s.target_annual_vol, bars_per_year=annualization(s.timeframe)),
                risk=RiskLimits(max_position_pct=s.max_position_pct, max_gross_exposure=s.max_gross_exposure, daily_loss_limit_pct=s.daily_loss_limit_pct, max_drawdown_kill_pct=s.max_drawdown_kill_pct, kelly_fraction=s.kelly_fraction, pdt_guard=s.pdt_guard),
                use_llm=bool(s.anthropic_api_key),
            )
            self.engine = LiveEngine(cfg, registry=self.registry)
        return self.engine


state = AppState()
