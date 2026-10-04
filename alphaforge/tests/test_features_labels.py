import numpy as np
import pandas as pd

from alphaforge.features import DEFAULT_FEATURE_SET, FeatureEngineer
from alphaforge.features.engineering import frac_diff_ffd
from alphaforge.labeling import sample_uniqueness_weights, triple_barrier_labels


def test_features_shape_and_causality(bars):
    df = bars["AAA"]
    fe = FeatureEngineer()
    f = fe.transform(df)
    assert list(f.columns) == DEFAULT_FEATURE_SET
    assert len(f) == len(df)
    # causality: features at t must not change when future bars are appended
    cut = len(df) - 50
    f_trunc = fe.transform(df.iloc[:cut])
    full_part = f.iloc[:cut]
    pd.testing.assert_frame_equal(f_trunc, full_part, check_exact=False, rtol=1e-9, atol=1e-12)


def test_features_mostly_finite(bars):
    f = FeatureEngineer().transform(bars["BBB"]).iloc[250:]
    assert f.isna().mean().max() < 0.05
    assert np.isfinite(f.dropna().to_numpy()).all()


def test_ffd_reduces_memory_but_keeps_signal():
    rng = np.random.default_rng(0)
    rw = pd.Series(np.cumsum(rng.standard_normal(2000)))
    d = frac_diff_ffd(rw, d=0.5).dropna()
    assert abs(d.autocorr(1)) < abs(rw.autocorr(1))
    assert d.std() > 0


def test_triple_barrier_labels(bars):
    tb = triple_barrier_labels(bars["AAA"]["close"], pt_mult=1.0, sl_mult=1.0, max_holding=10)
    valid = tb.dropna(subset=["ret"])
    assert set(valid["label"].unique()) <= {-1, 0, 1}
    assert set(valid["barrier"].unique()) <= {"pt", "sl", "time"}
    assert (valid["t_end"] <= 10).all() and (valid["t_end"] >= 1).all()
    # pt-hit rows must have positive return, sl-hit rows negative
    assert (valid.loc[valid.barrier == "pt", "ret"] > 0).all()
    assert (valid.loc[valid.barrier == "sl", "ret"] < 0).all()
    # trailing window is unlabeled (no peeking past the end)
    assert tb["ret"].iloc[-10:].isna().all()


def test_uniqueness_weights_positive():
    w = sample_uniqueness_weights(pd.Series([3, 3, 3, 1, 1, 0, 0]))
    assert (w >= 0).all() and abs(w.mean() - 1) < 1e-9
