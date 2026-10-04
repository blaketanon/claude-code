"""Triple-barrier labeling + meta-labeling + uniqueness weights (AFML style)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def daily_vol(close: pd.Series, span: int = 48) -> pd.Series:
    return np.log(close).diff().ewm(span=span, min_periods=span).std()


def triple_barrier_labels(
    close: pd.Series,
    pt_mult: float = 1.5,
    sl_mult: float = 1.0,
    max_holding: int = 12,
    vol_span: int = 48,
    min_ret: float = 0.0,
) -> pd.DataFrame:
    """For each bar, scan forward up to `max_holding` bars for a profit-take or stop-loss hit.

    Returns DataFrame with columns: ret (realized), label (+1/-1/0), t_end (int offset),
    barrier ('pt'|'sl'|'time'), target (vol used).
    """
    c = close.to_numpy(dtype=float)
    vol = daily_vol(close, vol_span).to_numpy()
    n = len(c)
    ret = np.full(n, np.nan)
    lab = np.zeros(n, dtype=int)
    tend = np.zeros(n, dtype=int)
    barrier = np.array([""] * n, dtype=object)
    for i in range(n):
        tgt = vol[i]
        if np.isnan(tgt) or tgt <= 0 or i + 1 >= n:
            continue
        up, dn = pt_mult * tgt, -sl_mult * tgt
        hit = None
        last = min(n - 1, i + max_holding)
        for j in range(i + 1, last + 1):
            r = np.log(c[j] / c[i])
            if r >= up:
                hit, b = j, "pt"
                break
            if r <= dn:
                hit, b = j, "sl"
                break
        if hit is None:
            hit, b = last, "time"
        r = np.log(c[hit] / c[i])
        ret[i] = r
        tend[i] = hit - i
        barrier[i] = b
        lab[i] = 1 if r > min_ret else (-1 if r < -min_ret else 0)
    out = pd.DataFrame({"ret": ret, "label": lab, "t_end": tend, "barrier": barrier, "target": vol}, index=close.index)
    # we can't know the outcome for the trailing window -> drop
    out.iloc[-max_holding:, out.columns.get_loc("ret")] = np.nan
    return out


def meta_labels(primary_side: pd.Series, tb: pd.DataFrame) -> pd.Series:
    """1 if taking the primary model's side would have been profitable, else 0."""
    return ((primary_side * tb["ret"]) > 0).astype(int).where(tb["ret"].notna())


def sample_uniqueness_weights(t_end: pd.Series) -> pd.Series:
    """Average uniqueness of overlapping label windows (approximation of AFML 4.x)."""
    n = len(t_end)
    conc = np.zeros(n)
    te = t_end.fillna(0).astype(int).to_numpy()
    for i in range(n):
        conc[i : i + te[i] + 1] += 1
    u = np.zeros(n)
    for i in range(n):
        span = conc[i : i + te[i] + 1]
        u[i] = np.mean(1.0 / span) if len(span) else 0.0
    w = pd.Series(u, index=t_end.index)
    return w / w.mean() if w.mean() > 0 else w
