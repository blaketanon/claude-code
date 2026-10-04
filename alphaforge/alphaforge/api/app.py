"""FastAPI application: admin API + nightly autopilot scheduler + static admin UI."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from alphaforge import __version__
from alphaforge.config import get_settings
from alphaforge.db import init_db
from .auth import bootstrap_admin
from .routers import auth, autopilot, backtests, data, models, trading, training
from .state import state

log = logging.getLogger(__name__)
UI_DIST = Path(__file__).resolve().parents[2] / "admin-ui" / "dist"


def _schedule_autopilot():
    from apscheduler.schedulers.background import BackgroundScheduler
    from alphaforge.training.autopilot import Autopilot, load_config

    sched = BackgroundScheduler(timezone="America/New_York")

    def nightly():
        cfg = load_config()
        if not cfg.enabled:
            return
        def run(ctx):
            out = Autopilot(cfg, registry=state.registry, progress=ctx.progress).run_cycle()
            if out.get("promoted") and state.engine:
                state.engine.load_champion()
            return out
        state.jobs.submit("autopilot", {"trigger": "schedule"}, run)

    sched.add_job(nightly, "cron", day_of_week="mon-fri", hour=17, minute=30, id="autopilot-nightly")
    sched.start()
    return sched


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    bootstrap_admin()
    sched = _schedule_autopilot()
    yield
    sched.shutdown(wait=False)
    if state.engine:
        state.engine.stop()


app = FastAPI(title="AlphaForge Admin API", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for r in (auth, trading, models, training, backtests, autopilot, data):
    app.include_router(r.router)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "version": __version__, "env": s.env, "trading_mode": s.trading_mode, "live_enabled": s.live_enabled}


if UI_DIST.exists():
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
