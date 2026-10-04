"""Leakage-aware cross-validation for overlapping financial labels."""
from __future__ import annotations

from typing import Iterator

import numpy as np
import pandas as pd


class PurgedKFold:
    """K-fold where training samples whose label window overlaps the test fold are purged,
    plus an embargo of `embargo_pct` of samples after each test fold."""

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01):
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    def split(self, t_end: pd.Series) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """`t_end` is indexed by sample position (0..n-1) and holds the END position of each label."""
        n = len(t_end)
        idx = np.arange(n)
        te = t_end.to_numpy()
        embargo = int(n * self.embargo_pct)
        folds = np.array_split(idx, self.n_splits)
        for test_idx in folds:
            t0, t1 = test_idx[0], test_idx[-1]
            test_end_max = int(np.nanmax(te[test_idx])) if len(test_idx) else t1
            train_mask = np.ones(n, dtype=bool)
            # purge: any train sample whose window [i, te[i]] touches [t0, t1]
            overlap = (idx <= t1) & (te >= t0)
            train_mask &= ~overlap
            # embargo after test
            train_mask[test_end_max + 1 : test_end_max + 1 + embargo] = False
            yield idx[train_mask], test_idx


def walk_forward_splits(
    n: int, n_folds: int = 4, min_train: int = 2000, embargo: int = 24
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Anchored walk-forward: train on [0, k), test on [k+embargo, k+step)."""
    if n <= min_train + embargo + 10:
        return [(np.arange(0, max(1, n - 10)), np.arange(max(1, n - 10), n))]
    step = (n - min_train) // n_folds
    out = []
    for k in range(min_train, n, step):
        test = np.arange(min(n, k + embargo), min(n, k + step))
        if len(test) < 10:
            break
        out.append((np.arange(0, k), test))
    return out
