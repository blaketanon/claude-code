"""AlphaForge command line."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(help="AlphaForge - AI day-trading platform", no_args_is_help=True)
data_app = typer.Typer(help="Market data")
model_app = typer.Typer(help="Model registry")
app.add_typer(data_app, name="data")
app.add_typer(model_app, name="models")


def _init():
    from alphaforge.db import init_db
    init_db()


@data_app.command("sync")
def data_sync(symbols: str = typer.Option(None), timeframe: str = typer.Option(None), days: int = 60, provider: str = typer.Option(None)):
    from alphaforge.config import get_settings
    from alphaforge.data import BarStore, get_provider
    _init()
    s = get_settings()
    syms = [x.strip().upper() for x in symbols.split(",")] if symbols else s.symbols
    store, prov = BarStore(), get_provider(provider)
    for sym in syms:
        df = store.sync(prov, sym, timeframe or s.timeframe, days)
        rprint(f"[green]{sym}[/green]: {len(df)} bars  {df.index[0] if len(df) else ''} -> {df.index[-1] if len(df) else ''}")


@app.command()
def train(symbols: str = typer.Option(None), timeframe: str = typer.Option(None), model_type: str = "lightgbm", hpo_trials: int = 0, days: int = 60, provider: str = typer.Option(None), promote: bool = typer.Option(False, help="Promote to champion after training")):
    from alphaforge.config import get_settings
    from alphaforge.data import BarStore, get_provider
    from alphaforge.models import ModelRegistry
    from alphaforge.training import TrainingConfig, TrainingPipeline
    _init()
    s = get_settings()
    syms = [x.strip().upper() for x in symbols.split(",")] if symbols else s.symbols
    tf = timeframe or s.timeframe
    store, prov = BarStore(), get_provider(provider)
    bars = {sym: store.sync(prov, sym, tf, days) for sym in syms}
    bars = {k: v for k, v in bars.items() if len(v) >= 200}
    cfg = TrainingConfig(symbols=list(bars), timeframe=tf, model_type=model_type, hpo_trials=hpo_trials)
    res = TrainingPipeline(cfg, progress=lambda p, m: rprint(f"[dim][{p:5.0%}][/dim] {m}")).run(bars)
    reg = ModelRegistry()
    rec = reg.register(res.model, strategy_key="default", timeframe=tf, symbols=list(bars), metrics=res.metrics, trained_from=res.trained_from, trained_to=res.trained_to, notes="[cli]")
    rprint(f"[bold green]registered {rec.model_id}[/bold green]")
    rprint(json.dumps(res.metrics["walk_forward"], indent=1))
    if promote or reg.champion() is None:
        reg.promote(rec.model_id, actor="cli")
        rprint("[bold]promoted to champion[/bold]")


@model_app.command("list")
def models_list():
    from alphaforge.models import ModelRegistry
    _init()
    t = Table("model_id", "type", "status", "sharpe", "dsr", "mdd", "created")
    for r in ModelRegistry().list():
        wf = r.metrics.get("walk_forward", {})
        t.add_row(r.model_id, r.model_type, r.status, f"{wf.get('sharpe', 0) or 0:.2f}", f"{wf.get('dsr', 0) or 0:.2f}", f"{wf.get('max_drawdown', 0) or 0:.2%}", r.created_at.strftime("%Y-%m-%d %H:%M"))
    rprint(t)


@model_app.command("promote")
def models_promote(model_id: str):
    from alphaforge.models import ModelRegistry
    _init()
    ModelRegistry().promote(model_id, actor="cli")
    rprint(f"[green]{model_id} is champion[/green]")


@app.command()
def backtest(model_id: str = typer.Option(None, help="defaults to champion"), slippage_bps: float = 1.0):
    import numpy as np
    import pandas as pd
    from alphaforge.backtest import Backtester, BacktestConfig
    from alphaforge.data import BarStore
    from alphaforge.features import FeatureEngineer
    from alphaforge.models import ModelRegistry
    from alphaforge.strategy import PolicyConfig, SignalPolicy
    from alphaforge.metrics.evaluation import annualization
    _init()
    reg = ModelRegistry()
    rec = reg.get(model_id) if model_id else reg.champion()
    if rec is None:
        raise typer.Exit("no model")
    model = reg.load(rec.model_id)
    fe = FeatureEngineer(model.feature_names)
    store = BarStore()
    bars = {s: store.load(s, rec.timeframe) for s in rec.symbols}
    prob = pd.DataFrame({s: pd.Series(model.predict_proba(fe.transform(b)), index=b.index) for s, b in bars.items()})
    rv = pd.DataFrame({s: np.log(b["close"]).diff().rolling(12).std() for s, b in bars.items()}).reindex(prob.index)
    w = SignalPolicy(PolicyConfig(bars_per_year=annualization(rec.timeframe))).weights(prob, rv)
    res = Backtester(BacktestConfig(timeframe=rec.timeframe, slippage_bps=slippage_bps)).run(bars, w)
    rprint(json.dumps(res.metrics, indent=1))


@app.command()
def autopilot(llm: bool = typer.Option(False, help="ask Claude for experiment proposals")):
    from alphaforge.training.autopilot import Autopilot, load_config
    _init()
    cfg = load_config()
    cfg.use_llm_research = llm or cfg.use_llm_research
    out = Autopilot(cfg).run_cycle()
    rprint(json.dumps(out, indent=1, default=str))


@app.command()
def run(paper: bool = typer.Option(True, help="paper broker (default). --no-paper requires live ack env vars"), once: bool = False, ignore_hours: bool = False):
    """Start the live trading engine in the foreground."""
    import logging, time
    from alphaforge.api.state import state
    from alphaforge.execution import get_broker
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _init()
    eng = state.get_engine()
    if paper:
        eng.broker = get_broker("paper")
        eng.oms.broker = eng.broker
    eng.cfg.respect_market_hours = not ignore_hours
    if not eng.load_champion():
        raise typer.Exit("no champion model. run `alphaforge train` first")
    if once:
        rprint(json.dumps(eng.step(), indent=1, default=str))
        return
    eng.start()
    rprint(f"[bold green]engine running[/bold green] mode={eng.broker.mode} model={eng.model_id}")
    try:
        while True:
            time.sleep(30)
            rprint({k: eng.status.get(k) for k in ("state", "last_bar", "cycles", "equity", "last_error")})
    except KeyboardInterrupt:
        eng.stop(flatten=True)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000, reload: bool = False):
    """Run the admin API (+ built admin UI if present)."""
    import uvicorn
    uvicorn.run("alphaforge.api.app:app", host=host, port=port, reload=reload)


@app.command()
def demo(days: int = 45):
    """End-to-end demo on synthetic data: sync -> train -> promote -> paper step."""
    import os
    os.environ.setdefault("ALPHAFORGE_DATA_PROVIDER", "synthetic")
    from alphaforge.config import get_settings
    get_settings.cache_clear()
    train(symbols="AAA,BBB,CCC,DDD", timeframe="5m", model_type="lightgbm", hpo_trials=3, days=days, provider="synthetic", promote=True)
    run(paper=True, once=True, ignore_hours=True)


if __name__ == "__main__":
    app()
