from datetime import datetime, timedelta, timezone

import pandas as pd

from alphaforge.execution import OrderManager, OrderRequest, PaperBroker
from alphaforge.risk import RiskLimits, RiskManager


def test_daily_loss_limit_and_kill_switch():
    rm = RiskManager(RiskLimits(daily_loss_limit_pct=0.02, max_drawdown_kill_pct=0.10))
    t0 = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    w = pd.Series({"A": 0.1, "B": -0.1})
    assert not rm.decide(w, 100_000, 0, t0).halted
    d = rm.decide(w, 97_900, 0.2, t0 + timedelta(minutes=5))
    assert d.halted and (d.weights == 0).all()
    # next day resumes
    assert not rm.decide(w, 97_900, 0, t0 + timedelta(days=1)).halted
    # drawdown kill is sticky across days
    d = rm.decide(w, 89_000, 0, t0 + timedelta(days=1, minutes=5))
    assert d.halted and rm.state.killed
    assert rm.decide(w, 100_000, 0, t0 + timedelta(days=3)).halted
    rm.reset_kill()
    assert not rm.decide(w, 100_000, 0, t0 + timedelta(days=3, minutes=5)).halted


def test_exposure_caps_and_kelly():
    rm = RiskManager(RiskLimits(max_position_pct=0.1, max_gross_exposure=0.25))
    w = pd.Series({"A": 0.5, "B": -0.5, "C": 0.2})
    out = rm.adjust_weights(w, 100_000, 0, datetime.now(timezone.utc))
    assert (out.abs() <= 0.1 + 1e-9).all() and out.abs().sum() <= 0.25 + 1e-9
    assert 0 <= rm.kelly_cap(0.55, 1.0, -1.0) <= 0.1
    assert rm.kelly_cap(0.4, 1.0, -1.0) == 0.0


def test_order_throttle():
    rm = RiskManager(RiskLimits(max_orders_per_minute=2))
    t = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    assert rm.allow_order(t) and rm.allow_order(t) and not rm.allow_order(t)
    assert rm.allow_order(t + timedelta(minutes=1))


def test_paper_broker_fills_and_pnl():
    b = PaperBroker(initial_cash=10_000, slippage_bps=0)
    b.set_marks({"X": 100.0})
    st = b.submit(OrderRequest("X", "buy", 10))
    assert st.status == "filled" and b.account().cash == 9_000
    b.set_marks({"X": 110.0})
    assert abs(b.account().equity - 10_100) < 1e-9
    b.submit(OrderRequest("X", "sell", 10))
    assert abs(b.account().cash - 10_100) < 1e-9 and not b.positions()


def test_oms_rebalance_hysteresis_and_round_trip():
    b = PaperBroker(initial_cash=100_000, slippage_bps=0)
    marks = {"X": 50.0, "Y": 200.0}
    b.set_marks(marks)
    oms = OrderManager(b, min_notional=50, min_rebalance_frac=0.15)
    out = oms.rebalance(pd.Series({"X": 0.1, "Y": -0.1}), marks)
    assert len(out) == 2
    pos = {p.symbol: p.qty for p in b.positions()}
    assert pos["X"] == 200 and pos["Y"] == -50
    # tiny drift ignored
    assert oms.rebalance(pd.Series({"X": 0.105, "Y": -0.1}), marks) == []
    # going flat always executes
    out = oms.rebalance(pd.Series({"X": 0.0, "Y": 0.0}), marks)
    assert len(out) == 2 and not b.positions()
