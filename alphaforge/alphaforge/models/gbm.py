"""Gradient-boosted trees (LightGBM) - the workhorse for tabular intraday features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SignalModel, register


@register
class GBMModel(SignalModel):
    model_type = "lightgbm"

    DEFAULTS = dict(
        objective="binary",
        learning_rate=0.03,
        num_leaves=31,
        max_depth=-1,
        min_child_samples=50,
        feature_fraction=0.8,
        bagging_fraction=0.8,
        bagging_freq=1,
        lambda_l2=1.0,
        n_estimators=400,
        verbosity=-1,
        n_jobs=4,
        random_state=42,
    )

    def __init__(self, feature_names, hyperparams=None):
        super().__init__(feature_names, {**self.DEFAULTS, **(hyperparams or {})})
        self._booster = None

    def fit(self, X, y, sample_weight=None):
        import lightgbm as lgb

        Xp = self._prep(X)
        mask = Xp.notna().all(axis=1) & y.notna()
        Xp, yb = Xp[mask], (y[mask] > 0).astype(int)
        sw = sample_weight[mask].to_numpy() if sample_weight is not None else None
        params = dict(self.hyperparams)
        n_estimators = int(params.pop("n_estimators"))
        self._booster = lgb.LGBMClassifier(n_estimators=n_estimators, **params)
        self._booster.fit(Xp, yb, sample_weight=sw)
        self.is_fitted = True
        return self

    def predict_proba(self, X):
        Xp = self._prep(X)
        out = np.full(len(Xp), 0.5)
        mask = Xp.notna().all(axis=1).to_numpy()
        if mask.any():
            out[mask] = self._booster.predict_proba(Xp[mask])[:, 1]
        return out

    def feature_importance(self):
        if self._booster is None:
            return {}
        imp = self._booster.booster_.feature_importance(importance_type="gain")
        tot = imp.sum() or 1.0
        return {f: float(v / tot) for f, v in zip(self.feature_names, imp)}
