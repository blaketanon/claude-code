"""Event-stepped multi-asset backtester with realistic frictions.

* Signals observed at bar t are executed at bar t+1 open (no look-ahead).
* Costs: commission per share, half-spread slippage in bps, plus volume-impact
  term proportional to participation.
* Intraday flattening before the close (day-trading constraint).
* Hooks for the same RiskManager used in live trading so backtests and live
  behave identically.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from alphaforge.metrics.evaluation import performance_stats


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    commission_per_share: float = 0.0
    slippage_bps: float = 1.0
    impact_coef: float = 0.1            # extra bps per 1% of bar volume traded
    max_participation: float = 0.05     # cap trade size at 5% of bar volume
    flatten_eod: bool = True
    eod_minutes_before_close: int = 10
    timeframe: str = "5m"
    allow_short: bool = True
    min_notional: float = 50.0          # ignore rebalances smaller than this (unless going flat)
    min_rebalance_frac: float = 0.15    # ignore drift < 15% of target position (hysteresis)


@dataclass
class BacktestResult:
    equity: pd.Series
    returns: pd.Series
    trades: pd.DataFrame
    positions: pd.DataFrame
    metrics: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return self.metrics


class Backtester:
    def __init__(self, cfg: BacktestConfig | None = None, risk_manager=None):
        self.cfg = cfg or BacktestConfig()
        self.risk = risk_manager

    def run(self, bars: dict[str, pd.DataFrame], target_weights: pd.DataFrame) -> BacktestResult:
        """
        bars: symbol -> OHLCV frame (UTC index). target_weights: index=ts, columns=symbols,
        desired fraction of equity per symbol as of bar close ts (executed next bar open).
        """
        symbols = [s for s in target_weights.columns if s in bars]
        ts_index = target_weights.index
        opens = pd.DataFrame({s: bars[s]["open"].reindex(ts_index) for s in symbols})
        closes = pd.DataFrame({s: bars[s]["close"].reindex(ts_index) for s in symbols})
        vols = pd.DataFrame({s: bars[s]["volume"].reindex(ts_index) for s in symbols})
        tw = target_weights[symbols].fillna(0.0)
        if not self.cfg.allow_short:
            tw = tw.clip(lower=0)

        ny = ts_index.tz_convert("America/New_York")
        mins = ny.hour * 60 + ny.minute
        near_close = mins >= (960 - self.cfg.eod_minutes_before_close)
        day = pd.Series(ny.date, index=ts_index)

        cash = self.cfg.initial_capital
        qty = {s: 0.0 for s in symbols}
        entry = {s: None for s in symbols}  # (price, time, qty)
        equity_hist, pos_hist, trades = [], [], []
        pending = None  # weights decided at previous bar close

        for i, ts in enumerate(ts_index):
            # 1) execute pending targets at this bar's open
            if pending is not None:
                px_open = opens.iloc[i]
                eq_prev = equity_hist[-1][1] if equity_hist else cash
                for s in symbols:
                    p = px_open[s]
                    if np.isnan(p) or p <= 0:
                        continue
                    target_qty = np.sign(pending[s]) * np.floor(abs(pending[s]) * eq_prev / p)
                    delta = target_qty - qty[s]
                    if delta == 0:
                        continue
                    if target_qty != 0 and (abs(delta) * p < self.cfg.min_notional or abs(delta) < self.cfg.min_rebalance_frac * abs(target_qty)):
                        continue
                    bar_vol = vols.iloc[i][s]
                    if not np.isnan(bar_vol) and bar_vol > 0:
                        cap = self.cfg.max_participation * bar_vol
                        delta = float(np.sign(delta) * min(abs(delta), cap))
                        part = abs(delta) / bar_vol
                    else:
                        part = 0.0
                    slip = (self.cfg.slippage_bps + self.cfg.impact_coef * part * 100) / 1e4
                    fill = p * (1 + slip) if delta > 0 else p * (1 - slip)
                    fee = abs(delta) * self.cfg.commission_per_share
                    cash -= delta * fill + fee
                    self._book(trades, entry, s, qty[s], delta, fill, ts, fee)
                    qty[s] += delta
            # 2) mark to market at close
            px_close = closes.iloc[i]
            mv = sum(qty[s] * px_close[s] for s in symbols if not np.isnan(px_close[s]))
            eq = cash + mv
            gross = sum(abs(qty[s] * px_close[s]) for s in symbols if not np.isnan(px_close[s]))
            equity_hist.append((ts, eq))
            pos_hist.append({"ts": ts, **{s: qty[s] * px_close[s] / eq if eq else 0 for s in symbols}})
            # 3) decide next targets
            w = tw.iloc[i].copy()
            is_last_of_day = (i + 1 >= len(ts_index)) or (day.iloc[i + 1] != day.iloc[i])
            if self.cfg.flatten_eod and (near_close[i] or is_last_of_day):
                w[:] = 0.0
            if self.risk is not None:
                w = self.risk.adjust_weights(w, equity=eq, gross_exposure=gross / eq if eq else 0, ts=ts)
            pending = w

        equity = pd.Series(dict(equity_hist), name="equity")
        rets = equity.pct_change().fillna(0.0)
        trades_df = pd.DataFrame(trades, columns=["symbol", "side", "qty", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "fees"])
        positions = pd.DataFrame(pos_hist).set_index("ts") if pos_hist else pd.DataFrame()
        metrics = performance_stats(rets.to_numpy(), equity.to_numpy(), self.cfg.timeframe, trades=trades_df)
        return BacktestResult(equity=equity, returns=rets, trades=trades_df, positions=positions, metrics=metrics)

    @staticmethod
    def _book(trades, entry, s, cur_qty, delta, fill, ts, fee):
        """Track round-trips (FIFO-ish, position-level)."""
        new_qty = cur_qty + delta
        if cur_qty == 0:
            entry[s] = (fill, ts, new_qty)
            return
        if np.sign(new_qty) == np.sign(cur_qty) and abs(new_qty) > abs(cur_qty):
            # adding - blend entry price
            p0, t0, q0 = entry[s]
            entry[s] = ((p0 * abs(q0) + fill * abs(delta)) / abs(new_qty), t0, new_qty)
            return
        # reducing / closing / flipping
        p0, t0, q0 = entry[s]
        closed = min(abs(cur_qty), abs(delta))
        side = "long" if cur_qty > 0 else "short"
        pnl = (fill - p0) * closed if cur_qty > 0 else (p0 - fill) * closed
        trades.append({"symbol": s, "side": side, "qty": closed, "entry_price": p0, "exit_price": fill,
                       "entry_time": t0, "exit_time": ts, "pnl": pnl - fee, "fees": fee})
        if new_qty == 0:
            entry[s] = None
        elif np.sign(new_qty) != np.sign(cur_qty):
            entry[s] = (fill, ts, new_qty)
        else:
            entry[s] = (p0, t0, new_qty)
