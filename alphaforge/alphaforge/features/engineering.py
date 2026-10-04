"""Feature engineering for intraday bars.

Design principles:
* Strictly causal: every feature at time t uses only bars <= t.
* Stationary-ish inputs (returns, z-scores, ratios) rather than raw prices.
* Fractionally-differentiated close (López de Prado, FFD) keeps memory while
  passing stationarity tests, which helps tree and deep models alike.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_FEATURE_SET = [
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_24",
    "logret_zs_24", "vol_12", "vol_48", "vol_ratio",
    "rsi_14", "macd", "macd_sig", "macd_hist",
    "bb_pos", "bb_width", "atr_14_pct",
    "vwap_dev", "vol_z_24", "dollar_vol_z",
    "hl_range", "close_loc", "gap",
    "roll_spread", "amihud", "ffd_close",
    "skew_24", "kurt_24", "autocorr_12",
    "tod_sin", "tod_cos", "mins_to_close",
    "ema_fast_slow", "mom_accel",
]


def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def ffd_weights(d: float, threshold: float = 1e-4, max_len: int = 200) -> np.ndarray:
    w = [1.0]
    for k in range(1, max_len):
        w_k = -w[-1] * (d - k + 1) / k
        if abs(w_k) < threshold:
            break
        w.append(w_k)
    return np.array(w[::-1])


def frac_diff_ffd(series: pd.Series, d: float = 0.4, threshold: float = 1e-4) -> pd.Series:
    """Fixed-width window fractional differentiation."""
    w = ffd_weights(d, threshold)
    width = len(w)
    vals = series.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    for i in range(width - 1, len(vals)):
        window = vals[i - width + 1 : i + 1]
        if np.isnan(window).any():
            continue
        out[i] = np.dot(w, window)
    return pd.Series(out, index=series.index)


class FeatureEngineer:
    def __init__(self, feature_set: list[str] | None = None, ffd_d: float = 0.4):
        self.feature_set = feature_set or DEFAULT_FEATURE_SET
        self.ffd_d = ffd_d

    # ------------------------------------------------------------------ #
    def transform(self, bars: pd.DataFrame) -> pd.DataFrame:
        """Return feature frame aligned on bars.index (NaN for warm-up rows)."""
        if bars.empty:
            return pd.DataFrame(columns=self.feature_set)
        o, h, l, c, v = (bars[k].astype(float) for k in ("open", "high", "low", "close", "volume"))
        f = pd.DataFrame(index=bars.index)
        logc = np.log(c)
        lr = logc.diff()

        for n in (1, 3, 6, 12, 24):
            f[f"ret_{n}"] = c.pct_change(n)
        f["logret_zs_24"] = (lr - lr.rolling(24).mean()) / lr.rolling(24).std()
        f["vol_12"] = lr.rolling(12).std()
        f["vol_48"] = lr.rolling(48).std()
        f["vol_ratio"] = f["vol_12"] / f["vol_48"]

        f["rsi_14"] = _rsi(c, 14) / 100.0
        ema12, ema26 = _ema(c, 12), _ema(c, 26)
        macd = (ema12 - ema26) / c
        f["macd"] = macd
        f["macd_sig"] = _ema(macd, 9)
        f["macd_hist"] = f["macd"] - f["macd_sig"]

        ma20, sd20 = c.rolling(20).mean(), c.rolling(20).std()
        f["bb_pos"] = (c - ma20) / (2 * sd20)
        f["bb_width"] = (4 * sd20) / ma20

        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        f["atr_14_pct"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / c

        # session-anchored VWAP
        day = bars.index.tz_convert("America/New_York").date if bars.index.tz is not None else bars.index.date
        day_key = pd.Series(day, index=bars.index)
        pv = (c * v).groupby(day_key.values).cumsum()
        vv = v.groupby(day_key.values).cumsum().replace(0, np.nan)
        vwap = pv / vv
        f["vwap_dev"] = (c - vwap) / vwap

        f["vol_z_24"] = (v - v.rolling(24).mean()) / v.rolling(24).std()
        dv = c * v
        f["dollar_vol_z"] = (dv - dv.rolling(48).mean()) / dv.rolling(48).std()

        f["hl_range"] = (h - l) / c
        f["close_loc"] = ((c - l) / (h - l).replace(0, np.nan)).fillna(0.5)
        f["gap"] = (o - c.shift()) / c.shift()

        # microstructure proxies
        cov = lr.rolling(24).apply(lambda x: np.cov(x[1:], x[:-1])[0, 1] if len(x) > 2 else np.nan, raw=True)
        f["roll_spread"] = 2 * np.sqrt((-cov).clip(lower=0))
        f["amihud"] = (lr.abs() / dv.replace(0, np.nan)).rolling(24).mean() * 1e9

        f["ffd_close"] = frac_diff_ffd(logc, d=self.ffd_d)

        f["skew_24"] = lr.rolling(24).skew()
        f["kurt_24"] = lr.rolling(24).kurt()
        f["autocorr_12"] = lr.rolling(24).apply(
            lambda x: pd.Series(x).autocorr(1) if np.std(x) > 0 else 0.0, raw=True
        )

        ny = bars.index.tz_convert("America/New_York")
        mins = pd.Series(ny.hour * 60 + ny.minute, index=bars.index, dtype=float)
        frac = ((mins - 570) / 390.0).clip(0, 1)
        f["tod_sin"] = np.sin(2 * np.pi * frac)
        f["tod_cos"] = np.cos(2 * np.pi * frac)
        f["mins_to_close"] = (960 - mins).clip(lower=0) / 390.0

        f["ema_fast_slow"] = (_ema(c, 8) - _ema(c, 21)) / c
        f["mom_accel"] = f["ret_6"] - f["ret_6"].shift(6)

        f = f.replace([np.inf, -np.inf], np.nan)
        return f[self.feature_set]

    def transform_many(self, bars_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Stack features for several symbols into a long frame with a (ts, symbol) MultiIndex."""
        frames = []
        for sym, bars in bars_by_symbol.items():
            if bars.empty:
                continue
            f = self.transform(bars)
            f["symbol"] = sym
            f["close"] = bars["close"].astype(float)
            frames.append(f)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames)
        out = out.set_index("symbol", append=True)
        return out.sort_index()
