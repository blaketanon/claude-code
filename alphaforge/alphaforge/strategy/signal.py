"""Turn model probabilities into target portfolio weights.

Sizing = sign(edge) * f(confidence) * vol-target scalar, clipped by policy limits.
Edge = 2p - 1 in [-1, 1]. Below `min_confidence` we are flat (avoid paying costs on noise).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class PolicyConfig:
    min_confidence: float = 0.55        # P(up) must exceed this (or be below 1-this) to act
    max_weight: float = 0.20            # per-symbol cap
    gross_cap: float = 1.0              # sum |w| cap
    target_vol: float = 0.15            # annualized portfolio vol target
    allow_short: bool = True
    regime_scalers: dict | None = None  # e.g. {"volatile": 0.5}
    bars_per_year: float = 252 * 78


class SignalPolicy:
    def __init__(self, cfg: PolicyConfig | None = None):
        self.cfg = cfg or PolicyConfig()

    def weights(
        self,
        prob_up: pd.DataFrame,          # index ts, columns symbols
        realized_vol: pd.DataFrame,     # same shape, per-bar return std
        regimes: pd.DataFrame | None = None,
        sentiment: pd.DataFrame | None = None,  # optional [-1,1] tilt from LLM analyst
    ) -> pd.DataFrame:
        c = self.cfg
        p = prob_up.clip(0, 1)
        edge = 2 * p - 1
        conf_thresh = 2 * c.min_confidence - 1
        active = edge.abs() >= conf_thresh
        # confidence scaling: linear from threshold to 1
        scale = ((edge.abs() - conf_thresh) / max(1e-9, 1 - conf_thresh)).clip(0, 1)
        raw = np.sign(edge) * scale
        if sentiment is not None:
            # dampen trades that fight strong contrary sentiment, boost aligned ones (max ±30%)
            tilt = 1 + 0.3 * np.sign(raw) * sentiment.reindex_like(raw).fillna(0)
            raw = raw * tilt.clip(0.5, 1.3)
        raw = raw.where(active, 0.0)
        if not c.allow_short:
            raw = raw.clip(lower=0)
        # vol targeting per symbol: w_i = raw_i * (target_vol / ann_vol_i)
        ann_vol = (realized_vol.reindex_like(raw) * np.sqrt(c.bars_per_year)).replace(0, np.nan)
        vol_scalar = (c.target_vol / ann_vol).clip(upper=3.0).fillna(0.0)
        w = (raw * vol_scalar).clip(-c.max_weight, c.max_weight)
        if regimes is not None and c.regime_scalers:
            sc = regimes.reindex_like(w).replace(c.regime_scalers).apply(pd.to_numeric, errors="coerce").fillna(1.0)
            w = w * sc
        gross = w.abs().sum(axis=1)
        over = gross > c.gross_cap
        w.loc[over] = w.loc[over].div(gross[over], axis=0) * c.gross_cap
        return w.fillna(0.0)
