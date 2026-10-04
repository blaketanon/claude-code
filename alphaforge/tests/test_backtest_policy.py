import numpy as np
import pandas as pd

from alphaforge.backtest import Backtester, BacktestConfig
from alphaforge.strategy import PolicyConfig, SignalPolicy


def test_policy_threshold_and_caps():
    idx = pd.date_range("2026-09-01 14:00", periods=3, freq="5min", tz="UTC")
    prob = pd.DataFrame({"A": [0.5, 0.9, 0.1], "B": [0.56, 0.99, 0.99]}, index=idx)
    rv = pd.DataFrame(0.001, index=idx, columns=["A", "B"])
    w = SignalPolicy(PolicyConfig(min_confidence=0.55, max_weight=0.3, gross_cap=0.5)).weights(prob, rv)
    assert w.loc[idx[0], "A"] == 0  # below confidence
    assert w.loc[idx[1], "A"] > 0 and w.loc[idx[2], "A"] < 0
    assert (w.abs() <= 0.3 + 1e-9).all().all()
    assert (w.abs().sum(axis=1) <= 0.5 + 1e-9).all()


def test_policy_no_short():
    idx = pd.date_range("2026-09-01 14:00", periods=1, freq="5min", tz="UTC")
    prob = pd.DataFrame({"A": [0.05]}, index=idx)
    rv = pd.DataFrame(0.001, index=idx, columns=["A"])
    w = SignalPolicy(PolicyConfig(allow_short=False)).weights(prob, rv)
    assert (w >= 0).all().all()


def test_backtester_executes_next_bar_and_flattens_eod(bars):
    b = {s: bars[s] for s in ["AAA", "BBB"]}
    idx = b["AAA"].index
    w = pd.DataFrame(0.0, index=idx, columns=["AAA", "BBB"])
    w.iloc[10:200, 0] = 0.1  # long AAA for a stretch
    res = Backtester(BacktestConfig(slippage_bps=0, commission_per_share=0)).run(b, w)
    assert len(res.equity) == len(idx)
    assert res.metrics["n_trades"] >= 1
    # positions flat at the end of every session
    ny = res.positions.index.tz_convert("America/New_York")
    last_of_day = res.positions.groupby(ny.date).tail(1)
    assert (last_of_day.abs() < 1e-9).all().all()
    # equity path is continuous (no NaN)
    assert res.equity.notna().all()


def test_backtester_costs_reduce_pnl(bars):
    b = {"AAA": bars["AAA"]}
    idx = b["AAA"].index
    rng = np.random.default_rng(0)
    w = pd.DataFrame(rng.choice([-0.1, 0, 0.1], size=len(idx)), index=idx, columns=["AAA"])
    free = Backtester(BacktestConfig(slippage_bps=0)).run(b, w).metrics["total_return"]
    costly = Backtester(BacktestConfig(slippage_bps=10)).run(b, w).metrics["total_return"]
    assert costly < free
