"""Pre-trade and portfolio-level risk controls. Shared by backtester and live engine.

Layers (all enforced every bar):
1. Hard kill switch (manual, or drawdown-from-peak breach)  -> flatten, refuse all orders.
2. Daily loss limit                                           -> flatten, no new risk until next session.
3. Per-symbol and gross exposure caps                         -> scale weights.
4. Fractional-Kelly cap on size given win-rate / payoff estimates.
5. Order throttles (max orders per minute, min re-trade interval) and PDT guard.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_position_pct: float = 0.20
    max_gross_exposure: float = 1.0
    daily_loss_limit_pct: float = 0.02
    max_drawdown_kill_pct: float = 0.10
    kelly_fraction: float = 0.25
    max_orders_per_minute: int = 20
    pdt_guard: bool = True
    pdt_equity_threshold: float = 25_000.0
    max_day_trades_per_5d: int = 3
    allow_short: bool = True


@dataclass
class RiskState:
    peak_equity: float = 0.0
    day_start_equity: float = 0.0
    current_day: str = ""
    halted_for_day: bool = False
    killed: bool = False
    kill_reason: str = ""
    orders_this_minute: int = 0
    minute_key: str = ""
    day_trades: list = field(default_factory=list)  # ISO dates of round-trips (for PDT)
    events: list = field(default_factory=list)


@dataclass
class RiskDecision:
    weights: pd.Series
    halted: bool
    reason: str = ""


class RiskManager:
    def __init__(self, limits: RiskLimits | None = None, on_event=None):
        self.limits = limits or RiskLimits()
        self.state = RiskState()
        self._on_event = on_event

    # ---- events ------------------------------------------------------- #
    def _event(self, level: str, code: str, message: str, **ctx):
        ev = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "code": code, "message": message, "context": ctx}
        self.state.events.append(ev)
        self.state.events = self.state.events[-500:]
        log.log(logging.WARNING if level in ("halt", "kill") else logging.INFO, "risk[%s] %s: %s", level, code, message)
        if self._on_event:
            try:
                self._on_event(ev)
            except Exception:  # never let telemetry break trading
                log.exception("risk event sink failed")

    # ---- controls ----------------------------------------------------- #
    def kill(self, reason: str = "manual"):
        if not self.state.killed:
            self.state.killed, self.state.kill_reason = True, reason
            self._event("kill", "KILL_SWITCH", f"kill switch engaged: {reason}")

    def reset_kill(self, actor: str = "admin"):
        self.state.killed, self.state.kill_reason = False, ""
        self._event("info", "KILL_RESET", f"kill switch reset by {actor}")

    def update_limits(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self.limits, k) and v is not None:
                setattr(self.limits, k, v)
        self._event("info", "LIMITS_UPDATED", "risk limits updated", **kwargs)

    # ---- per-bar evaluation ------------------------------------------- #
    def _roll_day(self, ts: datetime, equity: float):
        day = ts.astimezone(timezone.utc).strftime("%Y-%m-%d") if ts.tzinfo else ts.strftime("%Y-%m-%d")
        if day != self.state.current_day:
            self.state.current_day = day
            self.state.day_start_equity = equity
            if self.state.halted_for_day:
                self._event("info", "DAY_RESUME", "new session: daily halt lifted")
            self.state.halted_for_day = False
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity

    def check_portfolio(self, equity: float, ts: datetime) -> tuple[bool, str]:
        """Returns (halted, reason)."""
        self._roll_day(ts, equity)
        L, S = self.limits, self.state
        if S.killed:
            return True, f"killed: {S.kill_reason}"
        if S.peak_equity > 0 and equity / S.peak_equity - 1 <= -L.max_drawdown_kill_pct:
            self.kill(f"drawdown {equity / S.peak_equity - 1:.2%} breached {-L.max_drawdown_kill_pct:.0%}")
            return True, S.kill_reason
        if S.day_start_equity > 0 and equity / S.day_start_equity - 1 <= -L.daily_loss_limit_pct:
            if not S.halted_for_day:
                S.halted_for_day = True
                self._event("halt", "DAILY_LOSS_LIMIT", f"daily loss {equity / S.day_start_equity - 1:.2%} hit limit", equity=equity)
            return True, "daily loss limit"
        if S.halted_for_day:
            return True, "daily loss limit"
        return False, ""

    def adjust_weights(self, weights: pd.Series, equity: float, gross_exposure: float, ts: datetime) -> pd.Series:
        halted, reason = self.check_portfolio(equity, ts)
        w = weights.astype(float).fillna(0.0)
        if halted:
            return w * 0.0
        L = self.limits
        if not L.allow_short:
            w = w.clip(lower=0)
        w = w.clip(-L.max_position_pct, L.max_position_pct)
        g = w.abs().sum()
        if g > L.max_gross_exposure and g > 0:
            w = w / g * L.max_gross_exposure
        if L.pdt_guard and equity < L.pdt_equity_threshold and self._pdt_exhausted(ts):
            # can only hold existing positions; no new round trips
            self._event("warn", "PDT_GUARD", "pattern-day-trader limit reached; suppressing new positions")
            return w * 0.0
        return w

    def decide(self, weights: pd.Series, equity: float, gross_exposure: float, ts: datetime) -> RiskDecision:
        halted, reason = self.check_portfolio(equity, ts)
        return RiskDecision(self.adjust_weights(weights, equity, gross_exposure, ts), halted, reason)

    # ---- Kelly sizing helper ------------------------------------------ #
    def kelly_cap(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Fractional Kelly as a cap on per-position weight."""
        if avg_loss >= 0 or avg_win <= 0 or not (0 < win_rate < 1):
            return self.limits.max_position_pct
        b = avg_win / abs(avg_loss)
        k = win_rate - (1 - win_rate) / b
        return float(np.clip(k * self.limits.kelly_fraction, 0, self.limits.max_position_pct))

    # ---- order throttles ---------------------------------------------- #
    def allow_order(self, ts: datetime) -> bool:
        key = ts.strftime("%Y-%m-%d %H:%M")
        if key != self.state.minute_key:
            self.state.minute_key, self.state.orders_this_minute = key, 0
        if self.state.orders_this_minute >= self.limits.max_orders_per_minute:
            self._event("warn", "ORDER_THROTTLE", "max orders per minute reached")
            return False
        self.state.orders_this_minute += 1
        return True

    def record_day_trade(self, ts: datetime):
        self.state.day_trades.append(ts.strftime("%Y-%m-%d"))
        self.state.day_trades = self.state.day_trades[-200:]

    def _pdt_exhausted(self, ts: datetime) -> bool:
        cutoff = (pd.Timestamp(ts) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        recent = [d for d in self.state.day_trades if d >= cutoff]
        return len(recent) >= self.limits.max_day_trades_per_5d

    def snapshot(self) -> dict:
        return {"limits": asdict(self.limits), "state": {k: v for k, v in asdict(self.state).items() if k != "events"}, "recent_events": self.state.events[-20:]}
