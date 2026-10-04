from fastapi import APIRouter, Depends
from pydantic import BaseModel

from alphaforge.api.auth import current_user, require_admin
from alphaforge.api.state import state
from alphaforge.config import get_settings
from alphaforge.data import BarStore, get_provider

router = APIRouter(prefix="/api/data", tags=["data"])


class SyncBody(BaseModel):
    symbols: list[str] | None = None
    timeframe: str | None = None
    lookback_days: int = 60
    provider: str | None = None


@router.post("/sync")
def sync(body: SyncBody, user=Depends(require_admin)):
    s = get_settings()
    symbols, tf = body.symbols or s.symbols, body.timeframe or s.timeframe

    def run(ctx):
        store, prov = BarStore(), get_provider(body.provider)
        out = {}
        for i, sym in enumerate(symbols):
            ctx.progress(i / len(symbols), f"syncing {sym}")
            df = store.sync(prov, sym, tf, body.lookback_days)
            out[sym] = {"rows": int(len(df)), "from": df.index[0].isoformat() if len(df) else None, "to": df.index[-1].isoformat() if len(df) else None}
        return out

    return {"job_id": state.jobs.submit("data_sync", body.model_dump(), run)}


@router.get("/coverage")
def coverage(timeframe: str | None = None, user=Depends(current_user)):
    s = get_settings()
    tf = timeframe or s.timeframe
    store = BarStore()
    out = []
    for sym in store.symbols(tf):
        df = store.load(sym, tf)
        out.append({"symbol": sym, "rows": int(len(df)), "from": df.index[0].isoformat() if len(df) else None, "to": df.index[-1].isoformat() if len(df) else None})
    return {"timeframe": tf, "provider": s.data_provider, "symbols": out}


@router.get("/bars/{symbol}")
def bars(symbol: str, timeframe: str | None = None, limit: int = 500, user=Depends(current_user)):
    s = get_settings()
    df = BarStore().load(symbol.upper(), timeframe or s.timeframe).tail(limit)
    return [{"ts": ts.isoformat(), **{k: float(v) for k, v in row.items()}} for ts, row in df.iterrows()]
