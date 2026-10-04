"""Market regime detection with a Gaussian HMM on (return, realized-vol) features."""
from __future__ import annotations

import numpy as np
import pandas as pd

REGIME_NAMES = {0: "calm", 1: "volatile"}


class RegimeDetector:
    def __init__(self, n_states: int = 2, window: int = 12):
        self.n_states = n_states
        self.window = window
        self._hmm = None
        self._order = None

    def _obs(self, close: pd.Series) -> np.ndarray:
        lr = np.log(close).diff()
        vol = lr.rolling(self.window).std()
        X = np.column_stack([lr.to_numpy(), vol.to_numpy()])
        return X

    def fit(self, close: pd.Series) -> "RegimeDetector":
        from hmmlearn.hmm import GaussianHMM

        X = self._obs(close)
        mask = ~np.isnan(X).any(axis=1)
        if mask.sum() < 50:
            return self
        hmm = GaussianHMM(n_components=self.n_states, covariance_type="full", n_iter=200, random_state=1)
        hmm.fit(X[mask])
        # order states by volatility so 0 == calm
        self._order = np.argsort(hmm.means_[:, 1])
        self._hmm = hmm
        return self

    def predict(self, close: pd.Series) -> pd.Series:
        if self._hmm is None:
            return pd.Series("unknown", index=close.index)
        X = self._obs(close)
        mask = ~np.isnan(X).any(axis=1)
        states = np.full(len(X), -1)
        if mask.sum():
            raw = self._hmm.predict(X[mask])
            remap = {int(s): int(i) for i, s in enumerate(self._order)}
            states[mask] = [remap[int(s)] for s in raw]
        names = [REGIME_NAMES.get(s, "unknown") if s >= 0 else "unknown" for s in states]
        return pd.Series(names, index=close.index)
