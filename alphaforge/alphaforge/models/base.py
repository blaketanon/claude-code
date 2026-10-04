"""Model interface. Every model maps a feature matrix to P(up-move) per row."""
from __future__ import annotations

import json
import pickle
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


class SignalModel(ABC):
    model_type: str = "base"

    def __init__(self, feature_names: list[str], hyperparams: dict[str, Any] | None = None):
        self.feature_names = list(feature_names)
        self.hyperparams = dict(hyperparams or {})
        self.is_fitted = False

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series, sample_weight: pd.Series | None = None) -> "SignalModel": ...

    @abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Return P(label == 1) as a 1-D array aligned with X.index."""

    def feature_importance(self) -> dict[str, float]:
        return {}

    # ---- persistence ---------------------------------------------------- #
    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        meta = {"model_type": self.model_type, "feature_names": self.feature_names, "hyperparams": self.hyperparams}
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        self._save_weights(path)

    def _save_weights(self, path: Path) -> None:
        with open(path / "model.pkl", "wb") as fh:
            pickle.dump(self, fh)

    @classmethod
    def _load_weights(cls, path: Path, meta: dict) -> "SignalModel":
        with open(path / "model.pkl", "rb") as fh:
            return pickle.load(fh)

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        return X.reindex(columns=self.feature_names).astype(float)


def load_model(path: Path) -> SignalModel:
    from . import gbm, ensemble  # noqa: F401  (register subclasses)

    meta = json.loads((Path(path) / "meta.json").read_text())
    cls = _REGISTRY[meta["model_type"]]
    return cls._load_weights(Path(path), meta)


_REGISTRY: dict[str, type[SignalModel]] = {}


def register(cls: type[SignalModel]) -> type[SignalModel]:
    _REGISTRY[cls.model_type] = cls
    return cls


def available_model_types() -> list[str]:
    from . import gbm, ensemble  # noqa: F401

    try:
        from . import deep  # noqa: F401
    except Exception:  # torch not installed
        pass
    return sorted(_REGISTRY)
