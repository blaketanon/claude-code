"""End-to-end training: data -> features -> labels -> purged CV -> HPO -> walk-forward backtest -> registry."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np
import pandas as pd

from alphaforge.backtest import Backtester, BacktestConfig
from alphaforge.features import DEFAULT_FEATURE_SET, FeatureEngineer
from alphaforge.labeling import sample_uniqueness_weights, triple_barrier_labels
from alphaforge.models.base import SignalModel, _REGISTRY, available_model_types
from alphaforge.strategy import PolicyConfig, SignalPolicy
from .cv import PurgedKFold, walk_forward_splits
from alphaforge.metrics.evaluation import annualization, classification_stats, performance_stats

log = logging.getLogger(__name__)


@dataclass
class TrainingConfig:
    symbols: list[str]
    timeframe: str = "5m"
    model_type: str = "lightgbm"
    feature_set: list[str] = field(default_factory=lambda: list(DEFAULT_FEATURE_SET))
    hyperparams: dict = field(default_factory=dict)
    label: dict = field(default_factory=lambda: {"pt_mult": 1.5, "sl_mult": 1.0, "max_holding": 12})
    cv_splits: int = 4
    embargo_pct: float = 0.01
    hpo_trials: int = 0                 # 0 disables Optuna
    walk_forward_folds: int = 3
    policy: dict = field(default_factory=dict)
    strategy_key: str = "default"
    use_sample_weights: bool = True


@dataclass
class TrainingResult:
    model: SignalModel
    metrics: dict
    config: TrainingConfig
    trained_from: datetime | None
    trained_to: datetime | None
    oos_returns: pd.Series | None = None

    def to_dict(self) -> dict:
        return {"metrics": self.metrics, "config": asdict(self.config)}


class TrainingPipeline:
    def __init__(self, cfg: TrainingConfig, progress: Callable[[float, str], None] | None = None):
        self.cfg = cfg
        self.fe = FeatureEngineer(cfg.feature_set)
        self._progress = progress or (lambda p, m: None)

    # ------------------------------------------------------------------ #
    def build_dataset(self, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
        """Long frame (ts, symbol) with features + label columns."""
        frames = []
        for sym, df in bars.items():
            if df is None or len(df) < 200:
                continue
            f = self.fe.transform(df)
            tb = triple_barrier_labels(df["close"], **self.cfg.label)
            f["y"] = tb["label"].where(tb["ret"].notna())
            f["y_ret"] = tb["ret"]
            f["t_end"] = tb["t_end"]
            f["symbol"] = sym
            f["close"] = df["close"]
            f["rv"] = np.log(df["close"]).diff().rolling(12).std()
            frames.append(f)
        if not frames:
            raise ValueError("no symbol has enough bars (need >= 200)")
        ds = pd.concat(frames)
        ds.index.name = "ts"
        ds = ds.reset_index().sort_values(["ts", "symbol"]).set_index(["ts", "symbol"])
        return ds

    def _xyw(self, ds: pd.DataFrame):
        X = ds[self.cfg.feature_set]
        y = ds["y"]
        w = None
        if self.cfg.use_sample_weights:
            w = pd.Series(1.0, index=ds.index)
            for sym, grp in ds.groupby(level="symbol"):
                w.loc[grp.index] = sample_uniqueness_weights(grp["t_end"]).to_numpy()
        return X, y, w

    def _make_model(self, hyperparams: dict | None = None) -> SignalModel:
        available_model_types()
        cls = _REGISTRY[self.cfg.model_type]
        return cls(self.cfg.feature_set, {**self.cfg.hyperparams, **(hyperparams or {})})

    # ------------------------------------------------------------------ #
    def cross_validate(self, ds: pd.DataFrame, hyperparams: dict | None = None) -> dict:
        X, y, w = self._xyw(ds)
        valid = y.notna() & X.notna().all(axis=1)
        X, y = X[valid], y[valid]
        w = w[valid] if w is not None else None
        # positions in time order; t_end as positional offsets (approx. across stacked symbols)
        order = np.argsort(X.index.get_level_values("ts").values, kind="stable")
        X, y = X.iloc[order], y.iloc[order]
        w = w.iloc[order] if w is not None else None
        t_end = pd.Series(np.arange(len(X)) + ds.loc[X.index, "t_end"].to_numpy() * len(self.cfg.symbols), index=range(len(X)))
        cv = PurgedKFold(self.cfg.cv_splits, self.cfg.embargo_pct)
        oof = np.full(len(X), np.nan)
        for tr, te in cv.split(t_end):
            m = self._make_model(hyperparams)
            m.fit(X.iloc[tr], y.iloc[tr], w.iloc[tr] if w is not None else None)
            oof[te] = m.predict_proba(X.iloc[te])
        mask = ~np.isnan(oof)
        stats = classification_stats(y.to_numpy()[mask], oof[mask])
        # economic proxy: mean label-return captured by taking sign(edge)
        edge = np.sign(2 * oof[mask] - 1)
        rets = ds.loc[X.index, "y_ret"].to_numpy()[mask]
        stats["edge_capture"] = float(np.nanmean(edge * rets))
        return stats

    def optimize(self, ds: pd.DataFrame, n_trials: int) -> dict:
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial: optuna.Trial):
            if self.cfg.model_type in ("lightgbm", "ensemble"):
                hp = {
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                    "num_leaves": trial.suggest_int("num_leaves", 7, 63),
                    "min_child_samples": trial.suggest_int("min_child_samples", 20, 200),
                    "feature_fraction": trial.suggest_float("feature_fraction", 0.5, 1.0),
                    "lambda_l2": trial.suggest_float("lambda_l2", 1e-3, 10, log=True),
                    "n_estimators": trial.suggest_int("n_estimators", 100, 600),
                }
                if self.cfg.model_type == "ensemble":
                    hp = {"members": [{"type": "lightgbm", "hyperparams": hp}, {"type": "lightgbm", "hyperparams": {**hp, "random_state": 7, "num_leaves": max(7, hp["num_leaves"] // 2)}}]}
            elif self.cfg.model_type == "tcn":
                hp = {
                    "seq_len": trial.suggest_categorical("seq_len", [16, 32, 64]),
                    "hidden": trial.suggest_categorical("hidden", [32, 64, 128]),
                    "dropout": trial.suggest_float("dropout", 0.0, 0.3),
                    "lr": trial.suggest_float("lr", 1e-4, 5e-3, log=True),
                }
            else:
                hp = {}
            stats = self.cross_validate(ds, hp)
            trial.set_user_attr("stats", stats)
            # maximize edge capture, penalize log-loss (calibration)
            return stats["edge_capture"] * 1e4 - 0.1 * stats["log_loss"]

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, callbacks=[lambda st, tr: self._progress(0.2 + 0.4 * tr.number / max(1, n_trials), f"HPO trial {tr.number}")])
        best = dict(study.best_params)
        if self.cfg.model_type == "ensemble":
            best = {"members": [{"type": "lightgbm", "hyperparams": best}, {"type": "lightgbm", "hyperparams": {**best, "random_state": 7}}]}
        return best

    # ------------------------------------------------------------------ #
    def walk_forward_backtest(self, ds: pd.DataFrame, bars: dict[str, pd.DataFrame], hyperparams: dict | None) -> tuple[dict, pd.Series]:
        """Train on expanding window, predict next segment, backtest OOS predictions with full frictions."""
        ts_all = ds.index.get_level_values("ts").unique().sort_values()
        splits = walk_forward_splits(len(ts_all), self.cfg.walk_forward_folds, min_train=min(2000, len(ts_all) // 2))
        prob_frames = []
        for k, (tr_pos, te_pos) in enumerate(splits):
            tr_ts, te_ts = ts_all[tr_pos], ts_all[te_pos]
            tr = ds.loc[ds.index.get_level_values("ts").isin(tr_ts)]
            te = ds.loc[ds.index.get_level_values("ts").isin(te_ts)]
            X, y, w = self._xyw(tr)
            valid = y.notna()
            m = self._make_model(hyperparams)
            m.fit(X[valid], y[valid], w[valid] if w is not None else None)
            p = m.predict_proba(te[self.cfg.feature_set])
            prob_frames.append(pd.Series(p, index=te.index))
            self._progress(0.6 + 0.3 * (k + 1) / len(splits), f"walk-forward fold {k + 1}/{len(splits)}")
        prob = pd.concat(prob_frames).unstack("symbol")
        rv = ds["rv"].unstack("symbol").reindex(prob.index)
        policy = SignalPolicy(PolicyConfig(bars_per_year=annualization(self.cfg.timeframe), **self.cfg.policy))
        weights = policy.weights(prob, rv)
        bt = Backtester(BacktestConfig(timeframe=self.cfg.timeframe))
        res = bt.run({s: bars[s] for s in prob.columns}, weights)
        return res.metrics, res.returns

    # ------------------------------------------------------------------ #
    def run(self, bars: dict[str, pd.DataFrame]) -> TrainingResult:
        self._progress(0.05, "building dataset")
        ds = self.build_dataset(bars)
        self._progress(0.15, f"dataset ready: {len(ds)} rows")
        hp = dict(self.cfg.hyperparams)
        n_trials_total = 1
        if self.cfg.hpo_trials > 0:
            hp.update(self.optimize(ds, self.cfg.hpo_trials))
            n_trials_total = self.cfg.hpo_trials
        cv_stats = self.cross_validate(ds, hp)
        self._progress(0.6, f"CV auc={cv_stats['auc']:.3f}")
        wf_metrics, oos_rets = self.walk_forward_backtest(ds, bars, hp)
        wf_metrics = performance_stats(oos_rets.to_numpy(), None, self.cfg.timeframe, n_trials=n_trials_total) | {
            k: v for k, v in wf_metrics.items() if k.startswith(("n_trades", "hit_rate", "profit_factor", "avg_", "expectancy"))
        }
        self._progress(0.92, "fitting final model on all data")
        X, y, w = self._xyw(ds)
        valid = y.notna()
        final = self._make_model(hp)
        final.fit(X[valid], y[valid], w[valid] if w is not None else None)
        ts = ds.index.get_level_values("ts")
        metrics = {"cv": cv_stats, "walk_forward": wf_metrics, "feature_importance": final.feature_importance(), "hpo_trials": self.cfg.hpo_trials, "n_rows": int(len(ds))}
        self._progress(1.0, "done")
        return TrainingResult(final, metrics, self.cfg, ts.min().to_pydatetime(), ts.max().to_pydatetime(), oos_rets)
