"""Performance statistics with multiple-testing awareness."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

BARS_PER_YEAR = {"1m": 252 * 390, "5m": 252 * 78, "15m": 252 * 26, "30m": 252 * 13, "1h": 252 * 7, "1d": 252}


def annualization(timeframe: str) -> float:
    return float(BARS_PER_YEAR.get(timeframe, 252 * 78))


def sharpe(returns: np.ndarray, periods: float) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * math.sqrt(periods))


def sortino(returns: np.ndarray, periods: float) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    dn = r[r < 0]
    if len(r) < 2 or len(dn) == 0 or dn.std() == 0:
        return 0.0
    return float(r.mean() / dn.std() * math.sqrt(periods))


def max_drawdown(equity: np.ndarray) -> float:
    e = np.asarray(equity, dtype=float)
    if len(e) == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    dd = e / peak - 1.0
    return float(dd.min())


def probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """PSR: P(true SR > benchmark) accounting for skew/kurtosis (Bailey & Lopez de Prado)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 10 or r.std() == 0:
        return 0.0
    sr = r.mean() / r.std()
    g3, g4 = stats.skew(r), stats.kurtosis(r, fisher=False)
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr**2))
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / denom
    return float(stats.norm.cdf(z))


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int, sr_variance: float | None = None) -> float:
    """DSR: PSR against the expected maximum SR from `n_trials` independent trials."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 10 or n_trials < 1:
        return 0.0
    v = sr_variance if sr_variance is not None else (1.0 / max(1, len(r)))
    emc = 0.5772156649
    n = max(2, n_trials)
    sr0 = math.sqrt(v) * ((1 - emc) * stats.norm.ppf(1 - 1 / n) + emc * stats.norm.ppf(1 - 1 / (n * math.e)))
    return probabilistic_sharpe_ratio(r, sr0)


def performance_stats(
    returns: pd.Series | np.ndarray,
    equity: np.ndarray | None = None,
    timeframe: str = "5m",
    n_trials: int = 1,
    trades: pd.DataFrame | None = None,
) -> dict:
    r = np.nan_to_num(np.asarray(returns, dtype=float))
    periods = annualization(timeframe)
    eq = np.asarray(equity, dtype=float) if equity is not None else np.cumprod(1 + r)
    total = float(eq[-1] / eq[0] - 1) if len(eq) > 1 and eq[0] else 0.0
    years = len(r) / periods if periods else 1
    cagr = float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else 0.0
    mdd = max_drawdown(eq)
    out = {
        "total_return": total,
        "cagr": cagr,
        "ann_vol": float(r.std() * math.sqrt(periods)),
        "sharpe": sharpe(r, periods),
        "sortino": sortino(r, periods),
        "max_drawdown": mdd,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else 0.0,
        "psr": probabilistic_sharpe_ratio(r),
        "dsr": deflated_sharpe_ratio(r, n_trials),
        "n_periods": int(len(r)),
        "pct_time_in_market": float((r != 0).mean()) if len(r) else 0.0,
    }
    if trades is not None and len(trades):
        pnl = trades["pnl"].to_numpy(dtype=float)
        wins, losses = pnl[pnl > 0], pnl[pnl < 0]
        if len(losses) and losses.sum() < 0:
            pf = float(wins.sum() / -losses.sum())
        else:
            pf = float("inf") if len(wins) else 0.0
        out.update(
            {
                "n_trades": int(len(pnl)),
                "hit_rate": float((pnl > 0).mean()),
                "avg_win": float(wins.mean()) if len(wins) else 0.0,
                "avg_loss": float(losses.mean()) if len(losses) else 0.0,
                "profit_factor": pf,
                "expectancy": float(pnl.mean()),
            }
        )
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in out.items()}


def classification_stats(y_true: np.ndarray, p: np.ndarray) -> dict:
    from sklearn.metrics import log_loss, roc_auc_score

    y = (np.asarray(y_true) > 0).astype(int)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    acc = float(((p > 0.5) == y).mean())
    if len(np.unique(y)) < 2:
        return {"auc": 0.5, "log_loss": float(log_loss(y, p, labels=[0, 1])), "accuracy": acc}
    return {"auc": float(roc_auc_score(y, p)), "log_loss": float(log_loss(y, p)), "accuracy": acc}
