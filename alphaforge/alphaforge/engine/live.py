"""Live/paper trading engine.

Each bar:  ingest -> features -> champion model -> regime + LLM tilt -> policy -> risk -> OMS -> persist.
Runs in its own thread; the admin API controls start/stop/kill and hot-swaps the champion model.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from alphaforge.config import get_settings
from alphaforge.data import BarStore, MarketDataProvider, get_provider
from alphaforge.data.providers import timeframe_seconds
from alphaforge.db import session_scope
from alphaforge.db.models import EquityPoint, RiskEvent, SignalLog
from alphaforge.execution import Broker, OrderManager, get_broker
from alphaforge.features import FeatureEngineer
from alphaforge.models import ModelRegistry
from alphaforge.risk import RiskLimits, RiskManager
from alphaforge.strategy import PolicyConfig, RegimeDetector, SignalPolicy
from alphaforge.metrics.evaluation import annualization
from .clock import MarketClock

log = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    symbols: list[str]
    timeframe: str = "5m"
    strategy_key: str = "default"
    lookback_bars: int = 400
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    risk: RiskLimits = field(default_factory=RiskLimits)
    use_llm: bool = False
    llm_refresh_minutes: int = 30
    respect_market_hours: bool = True
    loop_once: bool = False  # for tests


class LiveEngine:
    def __init__(self, cfg: EngineConfig, broker: Broker | None = None, provider: MarketDataProvider | None = None, registry: ModelRegistry | None = None, store: BarStore | None = None):
        self.cfg = cfg
        s = get_settings()
        self.broker = broker or get_broker()
        self.provider = provider or get_provider()
        self.registry = registry or ModelRegistry()
        self.store = store or BarStore()
        self.clock = MarketClock(eod_minutes_before_close=s.eod_flatten_minutes_before_close)
        self.risk = RiskManager(cfg.risk, on_event=self._persist_risk_event)
        self.policy = SignalPolicy(cfg.policy)
        self.fe: FeatureEngineer | None = None
        self.model = None
        self.model_id: str | None = None
        self.regime = RegimeDetector()
        self.oms = OrderManager(self.broker)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status = {"state": "stopped", "last_bar": None, "last_error": "", "cycles": 0, "mode": self.broker.mode, "model_id": None}
        self._llm = None
        self._sentiment: dict[str, float] = {}
        self._sentiment_ts: float = 0.0
        self.last_signals: dict[str, dict] = {}

    # ---- lifecycle ---------------------------------------------------- #
    def load_champion(self) -> bool:
        rec = self.registry.champion(self.cfg.strategy_key)
        if rec is None:
            log.warning("no champion model for %s", self.cfg.strategy_key)
            return False
        if rec.model_id == self.model_id:
            return True
        with self._lock:
            self.model = self.registry.load(rec.model_id)
            self.model_id = rec.model_id
            self.fe = FeatureEngineer(self.model.feature_names)
            self.oms.model_id = rec.model_id
            self.status["model_id"] = rec.model_id
        log.info("loaded champion %s", rec.model_id)
        return True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="alphaforge-engine", daemon=True)
        self._thread.start()
        self.status["state"] = "running"

    def stop(self, flatten: bool = False):
        self._stop.set()
        if flatten:
            self.oms.flatten("stop")
        self.status["state"] = "stopped"

    def kill(self, reason: str = "manual kill switch"):
        self.risk.kill(reason)
        try:
            self.broker.cancel_all()
            self.oms.flatten("kill")
        finally:
            self.stop()
            self.status["state"] = "killed"

    # ---- main loop ---------------------------------------------------- #
    def _run(self):
        tf_s = timeframe_seconds(self.cfg.timeframe)
        while not self._stop.is_set():
            try:
                if self.cfg.respect_market_hours and not self._market_open():
                    self.status["state"] = "waiting_for_open"
                    if self.cfg.loop_once:
                        break
                    self._stop.wait(min(300, max(5, (self.clock.next_open() - self.clock.now()).total_seconds())))
                    continue
                self.status["state"] = "running"
                self.step()
            except Exception as e:  # keep the loop alive; surface error in status
                log.exception("engine cycle failed")
                self.status["last_error"] = f"{type(e).__name__}: {e}"
            if self.cfg.loop_once:
                break
            self._stop.wait(self.clock.seconds_to_next_bar(tf_s))

    def _market_open(self) -> bool:
        b = self.broker.is_market_open()
        return self.clock.is_open() if b is None else b

    def step(self, now: datetime | None = None) -> dict:
        """One full decision cycle. Public so tests and the API can drive it synchronously."""
        now = now or datetime.now(timezone.utc)
        if self.model is None and not self.load_champion():
            return {"skipped": "no champion"}
        bars = {s: self.provider.fetch_latest(s, self.cfg.timeframe, self.cfg.lookback_bars) for s in self.cfg.symbols}
        bars = {s: b for s, b in bars.items() if b is not None and len(b) >= 60}
        if not bars:
            return {"skipped": "no data"}
        marks = {s: float(b["close"].iloc[-1]) for s, b in bars.items()}
        if hasattr(self.broker, "set_marks"):
            self.broker.set_marks(marks)
        acct = self.broker.account()
        gross = sum(abs(p.market_value) for p in self.broker.positions())

        # EOD: flatten and stop trading for the day
        if self.cfg.respect_market_hours and self.clock.in_eod_window(now):
            self.oms.flatten("eod")
            self._persist_equity(now, acct, 0.0)
            return {"eod_flatten": True}

        with self._lock:
            probs, rv, regimes = {}, {}, {}
            for s, b in bars.items():
                X = self.fe.transform(b)
                p = float(self.model.predict_proba(X.tail(1))[-1])
                probs[s] = p
                rv[s] = float(np.log(b["close"]).diff().rolling(12).std().iloc[-1])
                regimes[s] = self._regime_for(b)
        ts = max(b.index[-1] for b in bars.values())
        prob_df = pd.DataFrame([probs], index=[ts])
        rv_df = pd.DataFrame([rv], index=[ts])
        reg_df = pd.DataFrame([regimes], index=[ts])
        sent_df = self._sentiment_frame(ts, list(bars)) if self.cfg.use_llm else None
        self.policy.cfg.bars_per_year = annualization(self.cfg.timeframe)
        w = self.policy.weights(prob_df, rv_df, reg_df, sent_df).iloc[0]
        decision = self.risk.decide(w, acct.equity, gross / acct.equity if acct.equity else 0, now)
        if decision.halted:
            self.oms.flatten(decision.reason)
            orders = []
        else:
            orders = self.oms.rebalance(decision.weights, marks, now, allow_order=self.risk.allow_order)
        self._persist_signals(ts, probs, regimes, decision.weights)
        acct = self.broker.account()
        gross = sum(abs(p.market_value) for p in self.broker.positions())
        self._persist_equity(now, acct, gross)
        self.status.update({"last_bar": ts.isoformat(), "cycles": self.status["cycles"] + 1, "equity": acct.equity, "halted": decision.halted, "halt_reason": decision.reason})
        self.last_signals = {s: {"prob_up": probs[s], "regime": regimes[s], "weight": float(decision.weights.get(s, 0.0)), "sentiment": self._sentiment.get(s)} for s in bars}
        return {"ts": ts.isoformat(), "orders": len(orders), "weights": decision.weights.to_dict(), "halted": decision.halted}

    # ---- helpers ------------------------------------------------------ #
    def _regime_for(self, b: pd.DataFrame) -> str:
        try:
            if self.regime._hmm is None:
                self.regime.fit(b["close"])
            return str(self.regime.predict(b["close"]).iloc[-1])
        except Exception:
            return "unknown"

    def _sentiment_frame(self, ts, symbols) -> pd.DataFrame | None:
        if time.time() - self._sentiment_ts > self.cfg.llm_refresh_minutes * 60:
            try:
                from alphaforge.strategy.llm_analyst import LLMAnalyst

                self._llm = self._llm or LLMAnalyst()
                self._sentiment = self._llm.sentiment_scores(symbols)
                self._sentiment_ts = time.time()
            except Exception as e:
                log.warning("LLM analyst unavailable: %s", e)
                self._sentiment_ts = time.time()  # back off
        if not self._sentiment:
            return None
        return pd.DataFrame([{s: self._sentiment.get(s, 0.0) for s in symbols}], index=[ts])

    def _persist_equity(self, ts, acct, gross):
        try:
            with session_scope() as s:
                s.add(EquityPoint(ts=ts, equity=acct.equity, cash=acct.cash, gross_exposure=gross / acct.equity if acct.equity else 0.0, mode=self.broker.mode))
        except Exception:
            log.exception("persist equity failed")

    def _persist_signals(self, ts, probs, regimes, weights):
        try:
            with session_scope() as s:
                for sym, p in probs.items():
                    s.add(SignalLog(ts=ts.to_pydatetime(), symbol=sym, model_id=self.model_id or "", prob_up=p, confidence=abs(2 * p - 1), regime=regimes.get(sym, ""), target_weight=float(weights.get(sym, 0.0)), llm_sentiment=self._sentiment.get(sym)))
        except Exception:
            log.exception("persist signals failed")

    def _persist_risk_event(self, ev: dict):
        with session_scope() as s:
            s.add(RiskEvent(level=ev["level"], code=ev["code"], message=ev["message"], context=ev.get("context", {})))

    def snapshot(self) -> dict:
        acct = self.broker.account()
        return {
            **self.status,
            "account": {"equity": acct.equity, "cash": acct.cash, "buying_power": acct.buying_power},
            "positions": [p.__dict__ for p in self.broker.positions()],
            "signals": self.last_signals,
            "risk": self.risk.snapshot(),
            "market_open": self._market_open(),
        }
