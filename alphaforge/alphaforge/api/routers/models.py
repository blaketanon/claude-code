from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.models.base import available_model_types

router = APIRouter(prefix="/api/models", tags=["models"])


def _ser(r):
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.__dict__.items() if not k.startswith("_")}


@router.get("")
def list_models(strategy_key: str | None = None, status: str | None = None, user=Depends(current_user)):
    return [_ser(r) for r in state.registry.list(strategy_key, status)]


@router.get("/types")
def types(user=Depends(current_user)):
    return available_model_types()


@router.get("/champion")
def champion(strategy_key: str = "default", user=Depends(current_user)):
    r = state.registry.champion(strategy_key)
    return _ser(r) if r else None


@router.get("/{model_id}")
def get_model(model_id: str, user=Depends(current_user)):
    r = state.registry.get(model_id)
    if not r:
        raise HTTPException(404)
    return _ser(r)


@router.post("/{model_id}/promote")
def promote(model_id: str, user=Depends(require_admin)):
    r = state.registry.promote(model_id, actor=user["username"])
    if state.engine:
        state.engine.load_champion()  # hot swap
    return _ser(r)


class StatusBody(BaseModel):
    status: str


@router.post("/{model_id}/status")
def set_status(model_id: str, body: StatusBody, user=Depends(require_admin)):
    if body.status not in ("candidate", "challenger", "retired"):
        raise HTTPException(400, "use /promote for champion")
    return _ser(state.registry.set_status(model_id, body.status, actor=user["username"]))


@router.post("/rollback")
def rollback(strategy_key: str = "default", user=Depends(require_admin)):
    r = state.registry.rollback(strategy_key, actor=user["username"])
    if r is None:
        raise HTTPException(404, "no retired champion to roll back to")
    if state.engine:
        state.engine.load_champion()
    return _ser(r)
