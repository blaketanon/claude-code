import uuid
from datetime import datetime

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.backtest import Backtester, BacktestConfig
from alphaforge.config import get_settings
from alphaforge.data import BarStore
from alphaforge.db import session_scope
from alphaforge.db.models import BacktestRun
from alphaforge.features import FeatureEngineer
from alphaforge.risk import RiskLimits, RiskManager
from alphaforge.strategy import PolicyConfig, SignalPolicy
from alphaforge.metrics.evaluation import annualization

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestBody(BaseModel):
    model_id: str
    symbols: list[str] | None = None
    start: datetime | None = None
    end: datetime | None = None
    slippage_bps: float = 1.0
    commission_per_share: float = 0.0
    policy: dict = Field(default_factory=dict)
    risk: dict = Field(default_factory=dict)


@router.post("")
def run_backtest(body: BacktestBody, user=Depends(require_admin)):
    rec = state.registry.get(body.model_id)
    if not rec:
        raise HTTPException(404, "model not found")

    def run(ctx):
        model = state.registry.load(body.model_id)
        fe = FeatureEngineer(model.feature_names)
        store = BarStore()
        symbols = body.symbols or rec.symbols
        bars = {s: store.load(s, rec.timeframe) for s in symbols}
        bars = {s: b for s, b in bars.items() if len(b) > 100}
        if body.start or body.end:
            bars = {s: b.loc[body.start or b.index[0] : body.end or b.index[-1]] for s, b in bars.items()}
        probs, rvs = {}, {}
        for i, (s, b) in enumerate(bars.items()):
            ctx.progress(0.1 + 0.6 * i / len(bars), f"scoring {s}")
            probs[s] = pd.Series(model.predict_proba(fe.transform(b)), index=b.index)
            rvs[s] = np.log(b["close"]).diff().rolling(12).std()
        prob = pd.DataFrame(probs)
        rv = pd.DataFrame(rvs).reindex(prob.index)
        pol = SignalPolicy(PolicyConfig(bars_per_year=annualization(rec.timeframe), **body.policy))
        w = pol.weights(prob, rv)
        risk = RiskManager(RiskLimits(**body.risk)) if body.risk else None
        res = Backtester(BacktestConfig(timeframe=rec.timeframe, slippage_bps=body.slippage_bps, commission_per_share=body.commission_per_share, initial_capital=get_settings().initial_capital), risk_manager=risk).run(bars, w)
        ctx.progress(0.9, "persisting")
        step = max(1, len(res.equity) // 2000)
        curve = [[ts.isoformat(), float(v)] for ts, v in res.equity.iloc[::step].items()]
        run_id = uuid.uuid4().hex[:12]
        with session_scope() as s:
            s.add(BacktestRun(run_id=run_id, model_id=body.model_id, params=body.model_dump(mode="json"), metrics=res.metrics, equity_curve=curve, trade_count=len(res.trades)))
        return {"run_id": run_id, "metrics": res.metrics}

    return {"job_id": state.jobs.submit("backtest", body.model_dump(mode="json"), run)}


@router.get("")
def list_runs(limit: int = 50, user=Depends(current_user)):
    with session_scope() as s:
        rows = s.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)).all()
    return [{"run_id": r.run_id, "model_id": r.model_id, "metrics": r.metrics, "trade_count": r.trade_count, "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/{run_id}")
def get_run(run_id: str, user=Depends(current_user)):
    with session_scope() as s:
        r = s.scalar(select(BacktestRun).where(BacktestRun.run_id == run_id))
    if not r:
        raise HTTPException(404)
    return {"run_id": r.run_id, "model_id": r.model_id, "params": r.params, "metrics": r.metrics, "equity_curve": r.equity_curve, "trade_count": r.trade_count, "created_at": r.created_at.isoformat()}
