from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.config import get_settings
from alphaforge.db import session_scope
from alphaforge.db.models import AuditLog, EquityPoint, Order, RiskEvent, SignalLog, Trade

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/status")
def status(user=Depends(current_user)):
    eng = state.get_engine()
    s = get_settings()
    return {**eng.snapshot(), "trading_mode": s.trading_mode, "live_enabled": s.live_enabled, "symbols": eng.cfg.symbols, "timeframe": eng.cfg.timeframe}


@router.post("/start")
def start(user=Depends(require_admin)):
    eng = state.get_engine()
    if not eng.load_champion():
        return {"ok": False, "error": "no champion model - train and promote one first"}
    eng.start()
    _audit(user, "engine.start")
    return {"ok": True, "state": eng.status["state"]}


class StopBody(BaseModel):
    flatten: bool = True


@router.post("/stop")
def stop(body: StopBody, user=Depends(require_admin)):
    eng = state.get_engine()
    eng.stop(flatten=body.flatten)
    _audit(user, "engine.stop", {"flatten": body.flatten})
    return {"ok": True}


@router.post("/kill")
def kill(user=Depends(require_admin)):
    eng = state.get_engine()
    eng.kill(f"manual kill by {user['username']}")
    _audit(user, "engine.kill")
    return {"ok": True, "risk": eng.risk.snapshot()}


@router.post("/kill/reset")
def kill_reset(user=Depends(require_admin)):
    eng = state.get_engine()
    eng.risk.reset_kill(user["username"])
    eng.status["state"] = "stopped"
    _audit(user, "engine.kill_reset")
    return {"ok": True}


@router.post("/step")
def step(user=Depends(require_admin)):
    """Run one synchronous decision cycle (useful for paper testing outside market hours)."""
    eng = state.get_engine()
    eng.cfg.respect_market_hours = False
    try:
        return eng.step()
    finally:
        eng.cfg.respect_market_hours = True


class RiskBody(BaseModel):
    max_position_pct: float | None = None
    max_gross_exposure: float | None = None
    daily_loss_limit_pct: float | None = None
    max_drawdown_kill_pct: float | None = None
    kelly_fraction: float | None = None
    allow_short: bool | None = None
    pdt_guard: bool | None = None
    min_confidence: float | None = None
    target_vol: float | None = None


@router.put("/risk")
def update_risk(body: RiskBody, user=Depends(require_admin)):
    eng = state.get_engine()
    d = body.model_dump(exclude_none=True)
    pol = {k: d.pop(k) for k in ("min_confidence", "target_vol") if k in d}
    eng.risk.update_limits(**d)
    for k, v in pol.items():
        setattr(eng.policy.cfg, k, v)
    if "max_position_pct" in d:
        eng.policy.cfg.max_weight = d["max_position_pct"]
    if "allow_short" in d:
        eng.policy.cfg.allow_short = d["allow_short"]
    _audit(user, "risk.update", {**d, **pol})
    return eng.risk.snapshot()


@router.get("/equity")
def equity(hours: int = 24 * 7, user=Depends(current_user)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with session_scope() as s:
        rows = s.scalars(select(EquityPoint).where(EquityPoint.ts >= since).order_by(EquityPoint.ts)).all()
    return [{"ts": r.ts.isoformat(), "equity": r.equity, "cash": r.cash, "gross": r.gross_exposure} for r in rows]


@router.get("/trades")
def trades(limit: int = 200, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(Trade).order_by(Trade.exit_time.desc()).limit(limit)).all()
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.__dict__.items() if not k.startswith("_")} for r in rows]


@router.get("/orders")
def orders(limit: int = 200, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(Order).order_by(Order.created_at.desc()).limit(limit)).all()
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.__dict__.items() if not k.startswith("_")} for r in rows]


@router.get("/signals")
def signals(limit: int = 200, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(SignalLog).order_by(SignalLog.ts.desc()).limit(limit)).all()
    return [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.__dict__.items() if not k.startswith("_")} for r in rows]


@router.get("/risk-events")
def risk_events(limit: int = 100, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(RiskEvent).order_by(RiskEvent.ts.desc()).limit(limit)).all()
    return [{"ts": r.ts.isoformat(), "level": r.level, "code": r.code, "message": r.message, "context": r.context} for r in rows]


@router.get("/audit")
def audit(limit: int = 100, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).all()
    return [{"ts": r.ts.isoformat(), "actor": r.actor, "action": r.action, "detail": r.detail} for r in rows]


def _audit(user, action, detail=None):
    with session_scope() as s:
        s.add(AuditLog(actor=user["username"], action=action, detail=detail or {}))
