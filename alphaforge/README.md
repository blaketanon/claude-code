# AlphaForge — AI-powered, self-training day-trading platform

AlphaForge is a fully automated intraday equities trading system with an admin console for
training, evaluating, promoting and supervising machine-learning signal models. It ships with:

* **Research-grade ML pipeline** — triple-barrier labels, purged/embargoed cross-validation,
  Optuna hyper-parameter search, walk-forward backtests with realistic frictions, Probabilistic and
  Deflated Sharpe Ratios, LightGBM / ensemble / PyTorch-TCN models.
* **Self-training autopilot** — nightly drift detection, challenger training, statistically gated
  champion promotion with rollback, optional Claude-generated research experiments.
* **Live engine** — bar-by-bar loop with HMM regime detection, optional Claude news/sentiment tilt,
  volatility-targeted sizing, layered risk controls (kill switch, daily loss limit, drawdown kill,
  exposure caps, PDT guard, throttles), EOD flattening, Alpaca paper/live broker adapter.
* **Admin API + UI** — FastAPI with JWT auth and audit logging; React dashboard with equity curve,
  positions, signals, model registry, training jobs, backtests, autopilot configuration, risk limits,
  trades/orders/signals logs, light and dark themes.

> **Risk disclaimer.** This is software, not investment advice. Intraday trading can lose money
> quickly. Paper trading is the default; live trading is deliberately gated behind two environment
> variables. Validate on paper for a meaningful period before considering real capital.

## Quick start

```bash
cd alphaforge
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add "deep" for the PyTorch model: pip install -e ".[dev,deep]"
cp .env.example .env               # edit credentials / universe

# 60-second end-to-end demo on synthetic data (train → promote → one paper step)
alphaforge demo

# Admin API + UI (build the UI once with: cd admin-ui && npm ci && npm run build)
alphaforge serve                   # http://localhost:8000  (login: admin / ALPHAFORGE_ADMIN_PASSWORD)
```

Typical real-data workflow:

```bash
alphaforge data sync --days 60 --provider yfinance      # or alpaca
alphaforge train --hpo-trials 30 --promote              # walk-forward evaluated, registered, promoted
alphaforge backtest                                     # champion on stored bars with frictions
alphaforge run --paper                                  # start the engine (respects NYSE hours)
alphaforge autopilot                                    # one self-training cycle now (--llm for Claude proposals)
```

Docker: `docker compose up --build` (UI is built in the image; data persists in a volume).

## Tests

```bash
pytest -q        # 24 tests: feature causality, labels, purged CV, metrics, backtester, risk, OMS,
                 # pipeline + registry lifecycle, promotion gates, live engine, API auth/flow
```

## Configuration

All settings are environment variables (see `.env.example`). Key ones:

| Variable | Purpose |
|---|---|
| `ALPHAFORGE_TRADING_MODE` / `ALPHAFORGE_LIVE_TRADING_ACK` | `paper` by default; live needs both set |
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` / `ALPACA_PAPER` | Broker + real-time data |
| `ALPHAFORGE_DATA_PROVIDER` | `synthetic`, `yfinance`, `alpaca` |
| `ANTHROPIC_API_KEY` / `ALPHAFORGE_LLM_MODEL` | Enables the Claude analyst and research agent |
| `ALPHAFORGE_SYMBOLS` / `ALPHAFORGE_TIMEFRAME` | Universe and bar size (`1m`,`5m`,`15m`,…) |

Risk limits and autopilot gates are editable at runtime from the admin UI (persisted in the DB).

## Repository layout

```
alphaforge/
  config.py            settings
  data/                providers (synthetic, yfinance, alpaca) + parquet bar store
  features/            causal feature engineering (incl. fractional differentiation)
  labeling/            triple barrier, meta-labels, uniqueness weights
  models/              SignalModel interface, LightGBM, ensemble, TCN, registry
  training/            purged CV, pipeline (HPO + walk-forward), autopilot
  metrics/             Sharpe/Sortino/DD/PSR/DSR, classification stats
  backtest/            event-stepped multi-asset backtester with costs
  strategy/            policy (prob→weights), HMM regimes, Claude analyst + research agent
  risk/                RiskManager (kill switch, limits, PDT, throttles)
  execution/           Broker ABC, PaperBroker, AlpacaBroker, OrderManager
  engine/              market clock + LiveEngine
  api/                 FastAPI app, auth, jobs, routers
  cli.py               Typer CLI
admin-ui/              React + Vite admin console
tests/                 pytest suite
docs/ARCHITECTURE.md   design notes
```

See `docs/ARCHITECTURE.md` for the component diagram and the per-bar data flow.

## Roadmap ideas

* Postgres + Redis-backed job queue for multi-worker training; MLflow experiment tracking.
* Websocket streaming bars (Alpaca) instead of REST polling; level-2 microstructure features.
* Cross-sectional ranking model and portfolio optimiser; options and futures adapters.
* Reinforcement-learning execution agent for order placement (limit vs. market, child sizing).
