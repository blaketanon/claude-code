from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.config import get_settings
from alphaforge.data import BarStore, get_provider
from alphaforge.db import session_scope
from alphaforge.db.models import Job
from alphaforge.features import DEFAULT_FEATURE_SET
from alphaforge.training import TrainingConfig, TrainingPipeline

router = APIRouter(prefix="/api/training", tags=["training"])


class TrainBody(BaseModel):
    symbols: list[str] | None = None
    timeframe: str | None = None
    model_type: str = "lightgbm"
    feature_set: list[str] = Field(default_factory=lambda: list(DEFAULT_FEATURE_SET))
    hyperparams: dict = Field(default_factory=dict)
    label: dict = Field(default_factory=lambda: {"pt_mult": 1.5, "sl_mult": 1.0, "max_holding": 12})
    hpo_trials: int = 0
    walk_forward_folds: int = 3
    cv_splits: int = 4
    policy: dict = Field(default_factory=dict)
    strategy_key: str = "default"
    lookback_days: int = 60
    provider: str | None = None
    auto_promote_if_first: bool = True
    notes: str = ""


@router.post("/jobs")
def train(body: TrainBody, user=Depends(require_admin)):
    s = get_settings()
    symbols = body.symbols or s.symbols
    timeframe = body.timeframe or s.timeframe
    cfg = TrainingConfig(symbols=symbols, timeframe=timeframe, model_type=body.model_type, feature_set=body.feature_set, hyperparams=body.hyperparams, label=body.label, cv_splits=body.cv_splits, hpo_trials=body.hpo_trials, walk_forward_folds=body.walk_forward_folds, policy=body.policy, strategy_key=body.strategy_key)

    def run(ctx):
        provider = get_provider(body.provider)
        store = BarStore()
        bars = {}
        for i, sym in enumerate(symbols):
            ctx.progress(0.05 * i / len(symbols), f"syncing {sym}")
            bars[sym] = store.sync(provider, sym, timeframe, body.lookback_days)
        bars = {k: v for k, v in bars.items() if len(v) >= 200}
        res = TrainingPipeline(cfg, progress=lambda p, m: ctx.progress(0.05 + 0.9 * p, m)).run(bars)
        rec = state.registry.register(res.model, strategy_key=body.strategy_key, timeframe=timeframe, symbols=list(bars), metrics=res.metrics, trained_from=res.trained_from, trained_to=res.trained_to, notes=f"[human:{user['username']}] {body.notes}")
        if body.auto_promote_if_first and state.registry.champion(body.strategy_key) is None:
            state.registry.promote(rec.model_id, actor=user["username"])
        return {"model_id": rec.model_id, "metrics": {k: v for k, v in res.metrics.items() if k != "feature_importance"}}

    job_id = state.jobs.submit("train", body.model_dump(), run)
    return {"job_id": job_id}


@router.get("/jobs")
def jobs(kind: str | None = None, limit: int = 50, user=Depends(current_user)):
    with session_scope() as s:
        q = select(Job).order_by(Job.created_at.desc()).limit(limit)
        if kind:
            q = q.where(Job.kind == kind)
        rows = s.scalars(q).all()
    return [_ser(j) for j in rows]


@router.get("/jobs/{job_id}")
def job(job_id: str, user=Depends(current_user)):
    with session_scope() as s:
        j = s.scalar(select(Job).where(Job.job_id == job_id))
    if not j:
        raise HTTPException(404)
    return _ser(j)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, user=Depends(require_admin)):
    return {"ok": state.jobs.cancel(job_id)}


@router.get("/features")
def features(user=Depends(current_user)):
    return DEFAULT_FEATURE_SET


def _ser(j):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in j.__dict__.items() if not k.startswith("_")}
