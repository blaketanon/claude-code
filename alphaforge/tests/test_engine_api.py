from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from alphaforge.data import SyntheticProvider
from alphaforge.engine import EngineConfig, LiveEngine
from alphaforge.execution import PaperBroker
from alphaforge.models import ModelRegistry
from alphaforge.training import TrainingConfig, TrainingPipeline


@pytest.fixture(scope="module")
def champion(bars):
    reg = ModelRegistry()
    res = TrainingPipeline(TrainingConfig(symbols=list(bars), walk_forward_folds=2, cv_splits=3, hyperparams={"n_estimators": 60})).run(bars)
    rec = reg.register(res.model, strategy_key="default", timeframe="5m", symbols=list(bars), metrics=res.metrics, trained_from=res.trained_from, trained_to=res.trained_to)
    reg.promote(rec.model_id)
    return rec


def test_engine_step_paper(champion):
    broker = PaperBroker(initial_cash=100_000)
    eng = LiveEngine(EngineConfig(symbols=["AAA", "BBB", "CCC"], respect_market_hours=False, loop_once=True), broker=broker, provider=SyntheticProvider())
    out = eng.step(datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc))
    assert "weights" in out and eng.model_id == champion.model_id
    snap = eng.snapshot()
    assert snap["account"]["equity"] > 0 and "risk" in snap
    # kill switch flattens and blocks
    eng.kill("test")
    assert not broker.positions() and eng.risk.state.killed
    out2 = eng.step(datetime(2026, 9, 30, 18, 5, tzinfo=timezone.utc))
    assert out2["halted"] is True


def test_engine_eod_flatten(champion):
    broker = PaperBroker(initial_cash=100_000)
    eng = LiveEngine(EngineConfig(symbols=["AAA"], respect_market_hours=True), broker=broker, provider=SyntheticProvider())
    # 15:55 New York on a weekday == 19:55 UTC during DST
    out = eng.step(datetime(2026, 9, 30, 19, 55, tzinfo=timezone.utc))
    assert out.get("eod_flatten") is True


def test_api_auth_and_flow(champion):
    from alphaforge.api.app import app

    with TestClient(app) as c:
        assert c.get("/api/health").json()["ok"]
        assert c.get("/api/trading/status").status_code == 401
        tok = c.post("/api/auth/token", data={"username": "admin", "password": "test-pass"}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        st = c.get("/api/trading/status", headers=h).json()
        assert st["trading_mode"] == "paper"
        models = c.get("/api/models", headers=h).json()
        assert any(m["status"] == "champion" for m in models)
        assert c.get("/api/models/champion", headers=h).json()["model_id"] == champion.model_id
        step = c.post("/api/trading/step", headers=h).json()
        assert "weights" in step or "skipped" in step
        r = c.put("/api/trading/risk", json={"max_position_pct": 0.05, "min_confidence": 0.6}, headers=h).json()
        assert r["limits"]["max_position_pct"] == 0.05
        assert c.post("/api/trading/kill", headers=h).json()["ok"]
        assert c.post("/api/trading/kill/reset", headers=h).json()["ok"]
        cfg = c.get("/api/autopilot/config", headers=h).json()
        assert "min_dsr" in cfg
        exp = c.post("/api/autopilot/experiments", json={"hypothesis": "fewer features", "config": {"model_type": "lightgbm", "feature_set": ["ret_1", "ret_3", "rsi_14", "vol_12", "macd", "bb_pos", "vwap_dev", "tod_sin"]}}, headers=h).json()
        assert "experiment_id" in exp
        job = c.post("/api/training/jobs", json={"symbols": ["AAA", "BBB"], "hpo_trials": 0, "walk_forward_folds": 2, "hyperparams": {"n_estimators": 30}, "lookback_days": 20, "provider": "synthetic"}, headers=h).json()
        assert "job_id" in job
        import time
        for _ in range(120):
            j = c.get(f"/api/training/jobs/{job['job_id']}", headers=h).json()
            if j["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.5)
        assert j["status"] == "succeeded", j.get("error")
        assert c.get("/api/data/coverage", headers=h).json()["timeframe"] == "5m"
