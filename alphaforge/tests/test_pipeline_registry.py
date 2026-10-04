import pytest

from alphaforge.models import GBMModel, ModelRegistry
from alphaforge.training import TrainingConfig, TrainingPipeline
from alphaforge.training.autopilot import Autopilot, AutopilotConfig, feature_drift, population_stability_index


@pytest.fixture(scope="module")
def trained(bars):
    cfg = TrainingConfig(symbols=list(bars), hpo_trials=0, walk_forward_folds=2, cv_splits=3, hyperparams={"n_estimators": 60})
    return TrainingPipeline(cfg).run(bars)


def test_pipeline_produces_model_and_metrics(trained):
    assert trained.model.is_fitted
    assert 0 <= trained.metrics["cv"]["auc"] <= 1
    wf = trained.metrics["walk_forward"]
    assert "sharpe" in wf and "dsr" in wf and wf["n_periods"] > 0
    assert wf["n_trades"] > 0
    assert sum(trained.metrics["feature_importance"].values()) > 0.99


def test_registry_lifecycle(trained):
    reg = ModelRegistry()
    a = reg.register(trained.model, strategy_key="t", timeframe="5m", symbols=["AAA"], metrics=trained.metrics, trained_from=trained.trained_from, trained_to=trained.trained_to)
    b = reg.register(trained.model, strategy_key="t", timeframe="5m", symbols=["AAA"], metrics=trained.metrics, trained_from=trained.trained_from, trained_to=trained.trained_to)
    assert reg.champion("t") is None
    reg.promote(a.model_id)
    assert reg.champion("t").model_id == a.model_id
    reg.promote(b.model_id)
    assert reg.champion("t").model_id == b.model_id
    assert reg.get(a.model_id).status == "retired"
    reg.rollback("t")
    assert reg.champion("t").model_id == a.model_id
    loaded = reg.load(a.model_id)
    assert isinstance(loaded, GBMModel) and loaded.feature_names == trained.model.feature_names


def test_psi_and_drift(bars):
    import numpy as np

    rng = np.random.default_rng(0)
    assert population_stability_index(rng.normal(size=5000), rng.normal(size=5000)) < 0.05
    assert population_stability_index(rng.normal(size=5000), rng.normal(1.0, size=5000)) > 0.2
    d = feature_drift(bars, ["ret_1", "vol_12", "rsi_14"])
    assert set(d) == {"ret_1", "vol_12", "rsi_14"}


def test_promotion_gate():
    ap = Autopilot(AutopilotConfig(symbols=["AAA"], min_sharpe_improvement=0.25, min_dsr=0.9))
    champ = {"walk_forward": {"sharpe": 1.0, "max_drawdown": -0.05}}
    good = {"walk_forward": {"sharpe": 1.5, "dsr": 0.95, "max_drawdown": -0.05, "n_trades": 100}}
    assert ap.should_promote(champ, good)[0]
    assert not ap.should_promote(champ, {"walk_forward": {"sharpe": 1.1, "dsr": 0.95, "max_drawdown": -0.05, "n_trades": 100}})[0]
    assert not ap.should_promote(champ, {"walk_forward": {"sharpe": 2.0, "dsr": 0.5, "max_drawdown": -0.05, "n_trades": 100}})[0]
    assert not ap.should_promote(champ, {"walk_forward": {"sharpe": 2.0, "dsr": 0.95, "max_drawdown": -0.2, "n_trades": 100}})[0]
    assert not ap.should_promote(champ, {"walk_forward": {"sharpe": 2.0, "dsr": 0.95, "max_drawdown": -0.05, "n_trades": 5}})[0]
    assert ap.should_promote(None, good)[0]
