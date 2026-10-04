"""Stacked ensemble: average (or logistic-stack) of heterogeneous base models."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import SignalModel, register, _REGISTRY


@register
class EnsembleModel(SignalModel):
    model_type = "ensemble"

    def __init__(self, feature_names, hyperparams=None):
        hp = {"members": [{"type": "lightgbm", "hyperparams": {}}, {"type": "lightgbm", "hyperparams": {"num_leaves": 15, "learning_rate": 0.05, "random_state": 7}}],
              "weights": None, **(hyperparams or {})}
        super().__init__(feature_names, hp)
        self.members: list[SignalModel] = []

    def fit(self, X, y, sample_weight=None):
        self.members = []
        for spec in self.hyperparams["members"]:
            cls = _REGISTRY[spec["type"]]
            m = cls(self.feature_names, spec.get("hyperparams", {}))
            m.fit(X, y, sample_weight)
            self.members.append(m)
        self.is_fitted = True
        return self

    def predict_proba(self, X):
        preds = np.stack([m.predict_proba(X) for m in self.members])
        w = self.hyperparams.get("weights")
        if w:
            w = np.asarray(w, dtype=float)
            return (preds * w[:, None]).sum(0) / w.sum()
        return preds.mean(0)

    def feature_importance(self):
        agg: dict[str, float] = {}
        for m in self.members:
            for k, v in m.feature_importance().items():
                agg[k] = agg.get(k, 0.0) + v / len(self.members)
        return agg
