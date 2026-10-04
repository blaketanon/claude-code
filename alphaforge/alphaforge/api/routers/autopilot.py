import uuid
from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.db import session_scope
from alphaforge.db.models import Experiment
from alphaforge.training.autopilot import Autopilot, AutopilotConfig, load_config, save_config

router = APIRouter(prefix="/api/autopilot", tags=["autopilot"])


@router.get("/config")
def get_config(user=Depends(current_user)):
    return asdict(load_config())


@router.put("/config")
def put_config(body: dict, user=Depends(require_admin)):
    cfg = load_config()
    for k, v in body.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    save_config(cfg)
    return asdict(cfg)


@router.post("/run")
def run_now(user=Depends(require_admin)):
    def run(ctx):
        ap = Autopilot(load_config(), registry=state.registry, progress=ctx.progress)
        out = ap.run_cycle()
        if out.get("promoted") and state.engine:
            state.engine.load_champion()
        return out

    return {"job_id": state.jobs.submit("autopilot", {}, run)}


class ExperimentBody(BaseModel):
    hypothesis: str
    config: dict = Field(description="TrainingConfig overrides: model_type, feature_set, hyperparams, label, policy, hpo_trials")


@router.post("/experiments")
def queue_experiment(body: ExperimentBody, user=Depends(require_admin)):
    exp_id = uuid.uuid4().hex[:12]
    with session_scope() as s:
        s.add(Experiment(experiment_id=exp_id, source="human", hypothesis=body.hypothesis, config=body.config))
    return {"experiment_id": exp_id}


@router.get("/experiments")
def experiments(limit: int = 100, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(Experiment).order_by(Experiment.created_at.desc()).limit(limit)).all()
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.__dict__.items() if not k.startswith("_")} for r in rows]


@router.delete("/experiments/{experiment_id}")
def drop_experiment(experiment_id: str, user=Depends(require_admin)):
    with session_scope() as s:
        r = s.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
        if r:
            r.is_active = False
    return {"ok": True}
