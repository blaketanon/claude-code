import numpy as np
import pandas as pd

from alphaforge.training.cv import PurgedKFold, walk_forward_splits
from alphaforge.training.evaluation import deflated_sharpe_ratio, max_drawdown, performance_stats, probabilistic_sharpe_ratio, sharpe


def test_purged_kfold_no_overlap():
    n = 500
    t_end = pd.Series(np.arange(n) + 5)  # every label spans 5 bars
    for tr, te in PurgedKFold(5, embargo_pct=0.02).split(t_end):
        t0, t1 = te[0], te[-1]
        # no training sample's label window touches the test fold
        assert not (((tr <= t1) & (t_end.to_numpy()[tr] >= t0)).any())
        # embargo removed samples right after the test fold
        emb = int(n * 0.02)
        after = np.arange(t_end.to_numpy()[te].max() + 1, min(n, t_end.to_numpy()[te].max() + 1 + emb))
        assert not np.isin(after, tr).any()


def test_walk_forward_is_chronological():
    splits = walk_forward_splits(5000, n_folds=4, min_train=1000, embargo=24)
    assert len(splits) >= 3
    for tr, te in splits:
        assert tr.max() < te.min()
        assert te.min() - tr.max() > 24


def test_metrics_sanity():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0005, 0.01, 5000)
    assert sharpe(r, 252) > 0
    assert -1 <= max_drawdown(np.cumprod(1 + r)) <= 0
    assert 0 <= probabilistic_sharpe_ratio(r) <= 1
    # deflation for many trials should lower confidence
    assert deflated_sharpe_ratio(r, n_trials=100) <= probabilistic_sharpe_ratio(r) + 1e-9
    st = performance_stats(r, timeframe="1d")
    assert set(["sharpe", "sortino", "max_drawdown", "dsr", "psr"]) <= set(st)
