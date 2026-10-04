"""Autopilot: the system trains itself.

Closed loop, run on a schedule (default nightly after the close) or on demand:

  1. Sync data for the universe.
  2. Measure feature drift (PSI) and champion live-performance decay.
  3. Build a candidate queue: scheduled retrain on fresh data, HPO challenger,
     optional Claude-proposed experiments, any human-queued experiments.
  4. Train every candidate under the identical walk-forward harness.
  5. Promote a challenger only if it beats the champion on OOS Sharpe by a margin,
     its Deflated Sharpe clears a threshold (multiple-testing aware), and its
     drawdown is not worse. Everything else is logged; nothing is silently discarded.
  6. Guardrails: max promotions per week, cool-down after a rollback, manual
     approval mode toggle.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select

from alphaforge.config import get_settings
from alphaforge.data import BarStore, get_provider
from alphaforge.db import session_scope
from alphaforge.db.models import AuditLog, Experiment, ModelRecord, RuntimeSetting, Trade
from alphaforge.features import DEFAULT_FEATURE_SET, FeatureEngineer
from alphaforge.models import ModelRegistry
from .pipeline import TrainingConfig, TrainingPipeline

log = logging.getLogger(__name__)


@dataclass
class AutopilotConfig:
    enabled: bool = True
    strategy_key: str = "default"
    symbols: list[str] = field(default_factory=lambda: get_settings().symbols)
    timeframe: str = "5m"
    lookback_days: int = 90
    hpo_trials: int = 15
    use_llm_research: bool = False
    llm_proposals: int = 2
    # promotion gates
    min_sharpe_improvement: float = 0.25     # challenger.sharpe - champion.sharpe
    min_dsr: float = 0.90                    # deflated Sharpe probability
    max_drawdown_tolerance: float = 0.02     # challenger mdd may be at most this much worse
    require_manual_approval: bool = False    # if True: mark as 'challenger' and wait for admin
    max_promotions_per_week: int = 2
    # drift
    psi_warn: float = 0.2
    psi_retrain: float = 0.35


SETTINGS_KEY = "autopilot"


def load_config() -> AutopilotConfig:
    with session_scope() as s:
        row = s.get(RuntimeSetting, SETTINGS_KEY)
    cfg = AutopilotConfig()
    if row:
        for k, v in row.value.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
    return cfg


def save_config(cfg: AutopilotConfig) -> None:
    with session_scope() as s:
        row = s.get(RuntimeSetting, SETTINGS_KEY)
        if row:
            row.value = asdict(cfg)
        else:
            s.add(RuntimeSetting(key=SETTINGS_KEY, value=asdict(cfg)))


# ---------------------------------------------------------------------- #
def population_stability_index(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    e = expected[~np.isnan(expected)]
    a = actual[~np.isnan(actual)]
    if len(e) < 50 or len(a) < 50:
        return 0.0
    edges = np.quantile(e, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.histogram(e, edges)[0] / len(e) + 1e-6
    pa = np.histogram(a, edges)[0] / len(a) + 1e-6
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def feature_drift(bars: dict[str, pd.DataFrame], feature_set: list[str], recent_frac: float = 0.2) -> dict:
    fe = FeatureEngineer(feature_set)
    frames = [fe.transform(b) for b in bars.values() if len(b) > 200]
    if not frames:
        return {}
    X = pd.concat(frames).sort_index()
    n_recent = max(50, int(len(X) * recent_frac))
    ref, rec = X.iloc[:-n_recent], X.iloc[-n_recent:]
    return {c: population_stability_index(ref[c].to_numpy(dtype=float), rec[c].to_numpy(dtype=float)) for c in feature_set}


def live_performance(strategy_key: str, days: int = 10) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with session_scope() as s:
        rows = s.scalars(select(Trade).where(Trade.exit_time >= since)).all()
    if not rows:
        return {"n_trades": 0}
    pnl = np.array([t.pnl for t in rows])
    return {"n_trades": int(len(pnl)), "hit_rate": float((pnl > 0).mean()), "total_pnl": float(pnl.sum()), "expectancy": float(pnl.mean())}


# ---------------------------------------------------------------------- #
class Autopilot:
    def __init__(self, cfg: AutopilotConfig | None = None, registry: ModelRegistry | None = None, store: BarStore | None = None, provider=None, progress=None):
        self.cfg = cfg or load_config()
        self.registry = registry or ModelRegistry()
        self.store = store or BarStore()
        self.provider = provider or get_provider()
        self._progress = progress or (lambda p, m: log.info("[%.0f%%] %s", p * 100, m))

    # ---- data --------------------------------------------------------- #
    def sync_data(self) -> dict[str, pd.DataFrame]:
        out = {}
        for i, sym in enumerate(self.cfg.symbols):
            try:
                out[sym] = self.store.sync(self.provider, sym, self.cfg.timeframe, self.cfg.lookback_days)
            except Exception as e:
                log.warning("sync failed for %s: %s", sym, e)
                out[sym] = self.store.load(sym, self.cfg.timeframe)
            self._progress(0.1 * (i + 1) / len(self.cfg.symbols), f"synced {sym}")
        return {k: v for k, v in out.items() if v is not None and len(v) >= 200}

    # ---- candidate generation ------------------------------------------ #
    def candidates(self, champion: ModelRecord | None, drift: dict) -> list[dict]:
        base_features = champion.feature_set if champion else list(DEFAULT_FEATURE_SET)
        base_hp = champion.hyperparams if champion else {}
        cands = [
            {"source": "autopilot", "hypothesis": "Retrain champion architecture on the freshest window", "config": {"model_type": champion.model_type if champion else "lightgbm", "feature_set": base_features, "hyperparams": base_hp, "hpo_trials": 0}},
            {"source": "autopilot", "hypothesis": "Hyper-parameter search challenger", "config": {"model_type": "lightgbm", "feature_set": list(DEFAULT_FEATURE_SET), "hyperparams": {}, "hpo_trials": self.cfg.hpo_trials}},
        ]
        drifted = sorted((k for k, v in drift.items() if v >= self.cfg.psi_retrain), key=lambda k: -drift[k])
        if drifted:
            keep = [f for f in base_features if f not in set(drifted[:5])]
            if len(keep) >= 8:
                cands.append({"source": "autopilot", "hypothesis": f"Drop drifted features {drifted[:5]} (PSI >= {self.cfg.psi_retrain})", "config": {"model_type": "lightgbm", "feature_set": keep, "hyperparams": base_hp, "hpo_trials": 0}})
        if champion and champion.model_type != "ensemble":
            cands.append({"source": "autopilot", "hypothesis": "Diversified ensemble of GBMs reduces variance", "config": {"model_type": "ensemble", "feature_set": base_features, "hyperparams": {}, "hpo_trials": 0}})
        # human-queued experiments
        with session_scope() as s:
            queued = s.scalars(select(Experiment).where(Experiment.status == "proposed", Experiment.is_active.is_(True))).all()
        for q in queued:
            cands.append({"source": q.source, "hypothesis": q.hypothesis, "config": q.config, "experiment_id": q.experiment_id})
        if self.cfg.use_llm_research:
            try:
                from alphaforge.strategy.research_agent import ResearchAgent

                summary = {"metrics": champion.metrics, "hyperparams": champion.hyperparams, "features": champion.feature_set} if champion else {}
                recent = [{"hypothesis": e.hypothesis, "status": e.status, "result": e.result} for e in self.recent_experiments()]
                plan = ResearchAgent().propose(summary, recent, drift, n=self.cfg.llm_proposals)
                for p in plan.proposals:
                    cands.append({"source": "llm", "hypothesis": p.hypothesis, "config": {"model_type": p.model_type, "feature_set": p.feature_set, "hyperparams": p.hyperparams, "label": p.label, "policy": p.policy, "hpo_trials": 0}})
            except Exception as e:
                log.warning("LLM research unavailable: %s", e)
        return cands

    def recent_experiments(self, n: int = 20) -> list[Experiment]:
        with session_scope() as s:
            return list(s.scalars(select(Experiment).order_by(Experiment.created_at.desc()).limit(n)))

    # ---- promotion gate ------------------------------------------------ #
    def should_promote(self, champ: dict | None, chall: dict) -> tuple[bool, str]:
        wf = chall.get("walk_forward", {})
        if (wf.get("n_trades") or 0) < 30:
            return False, "too few OOS trades"
        if (wf.get("dsr") or 0) < self.cfg.min_dsr:
            return False, f"DSR {wf.get('dsr', 0):.2f} < {self.cfg.min_dsr}"
        if champ is None:
            return (wf.get("sharpe") or 0) > 0, "first champion" if (wf.get("sharpe") or 0) > 0 else "negative Sharpe"
        cwf = champ.get("walk_forward", {})
        if (wf.get("sharpe") or 0) - (cwf.get("sharpe") or 0) < self.cfg.min_sharpe_improvement:
            return False, f"Sharpe gain {(wf.get('sharpe') or 0) - (cwf.get('sharpe') or 0):.2f} < {self.cfg.min_sharpe_improvement}"
        if (wf.get("max_drawdown") or 0) < (cwf.get("max_drawdown") or 0) - self.cfg.max_drawdown_tolerance:
            return False, "drawdown worse than tolerance"
        return True, "passes all gates"

    def promotions_this_week(self) -> int:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        with session_scope() as s:
            return len(s.scalars(select(AuditLog).where(AuditLog.action == "model.promote", AuditLog.ts >= since, AuditLog.actor == "autopilot")).all())

    # ---- main cycle ---------------------------------------------------- #
    def run_cycle(self) -> dict:
        if not self.cfg.enabled:
            return {"skipped": "autopilot disabled"}
        bars = self.sync_data()
        if not bars:
            return {"error": "no data"}
        champion = self.registry.champion(self.cfg.strategy_key)
        drift = feature_drift(bars, champion.feature_set if champion else list(DEFAULT_FEATURE_SET))
        worst = sorted(drift.items(), key=lambda kv: -kv[1])[:5]
        self._progress(0.12, f"drift top: {worst}")
        live = live_performance(self.cfg.strategy_key)
        cands = self.candidates(champion, drift)
        results, promoted = [], None
        for i, c in enumerate(cands):
            exp_id = c.get("experiment_id") or uuid.uuid4().hex[:12]
            self._progress(0.15 + 0.8 * i / max(1, len(cands)), f"training candidate {i + 1}/{len(cands)}: {c['hypothesis']}")
            cfg = TrainingConfig(symbols=list(bars), timeframe=self.cfg.timeframe, strategy_key=self.cfg.strategy_key, **{k: v for k, v in c["config"].items() if k in TrainingConfig.__dataclass_fields__})
            try:
                res = TrainingPipeline(cfg).run(bars)
            except Exception as e:
                log.exception("candidate failed")
                self._record(exp_id, c, "rejected", {"error": str(e)}, None)
                results.append({"hypothesis": c["hypothesis"], "status": "failed", "error": str(e)})
                continue
            ok, why = self.should_promote(champion.metrics if champion else None, res.metrics)
            status = "candidate"
            if ok and promoted is None and self.promotions_this_week() < self.cfg.max_promotions_per_week:
                status = "challenger" if self.cfg.require_manual_approval else "champion"
            rec = self.registry.register(res.model, strategy_key=self.cfg.strategy_key, timeframe=self.cfg.timeframe, symbols=list(bars), metrics=res.metrics, trained_from=res.trained_from, trained_to=res.trained_to, notes=f"[{c['source']}] {c['hypothesis']} :: {why}", status="candidate")
            if status == "champion":
                self.registry.promote(rec.model_id, actor="autopilot")
                promoted = rec.model_id
                champion = self.registry.get(rec.model_id)
            elif status == "challenger":
                self.registry.set_status(rec.model_id, "challenger", actor="autopilot")
            self._record(exp_id, c, "accepted" if ok else "rejected", {"why": why, "walk_forward": res.metrics["walk_forward"], "cv": res.metrics["cv"]}, rec.model_id)
            results.append({"hypothesis": c["hypothesis"], "model_id": rec.model_id, "status": status, "why": why, "sharpe": res.metrics["walk_forward"].get("sharpe"), "dsr": res.metrics["walk_forward"].get("dsr")})
        self._progress(1.0, "cycle complete")
        return {"drift_top": worst, "live_performance": live, "candidates": results, "promoted": promoted, "champion": champion.model_id if champion else None}

    def _record(self, exp_id: str, c: dict, status: str, result: dict, model_id: str | None):
        with session_scope() as s:
            row = s.scalar(select(Experiment).where(Experiment.experiment_id == exp_id))
            if row is None:
                row = Experiment(experiment_id=exp_id, source=c["source"], hypothesis=c["hypothesis"], config=c["config"])
                s.add(row)
            row.status, row.result, row.model_id = status, result, model_id
