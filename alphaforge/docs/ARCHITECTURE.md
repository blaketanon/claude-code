# AlphaForge architecture

```
                 ┌──────────────────────────── Admin UI (React) ────────────────────────────┐
                 │ Dashboard · Models · Training · Backtests · Autopilot · Risk · Logs       │
                 └───────────────────────────────┬──────────────────────────────────────────┘
                                                 │ JWT / REST
┌────────────────────────────────────────────────▼────────────────────────────────────────────┐
│ FastAPI admin API  (alphaforge/api)                                                          │
│   routers: auth · trading · models · training · backtests · autopilot · data                 │
│   JobRunner (thread pool, DB-backed progress)   APScheduler (nightly autopilot)              │
└────────┬──────────────────────────────┬───────────────────────────────┬─────────────────────┘
         │                              │                               │
┌────────▼─────────┐        ┌───────────▼────────────┐        ┌─────────▼──────────────────┐
│ LiveEngine        │        │ TrainingPipeline        │        │ Autopilot                   │
│ (engine/live.py)  │        │ (training/pipeline.py)  │        │ (training/autopilot.py)     │
│ bar loop:         │        │ features → labels →     │        │ sync → drift → candidates → │
│ data → features → │        │ purged CV → Optuna →    │        │ train all → gate → promote  │
│ model → regime →  │        │ walk-forward backtest → │        │ (+ Claude research agent)   │
│ LLM tilt → policy │        │ final fit → registry    │        └─────────────────────────────┘
│ → risk → OMS      │        └─────────────────────────┘
└───┬─────────┬─────┘
    │         │
┌───▼───┐ ┌───▼────────┐   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐
│Broker │ │RiskManager │   │ ModelRegistry │  │ BarStore     │  │ SQLite/Postgres (SQLAlchemy)│
│paper  │ │kill switch │   │ champion /    │  │ parquet per  │  │ models·jobs·orders·trades·  │
│alpaca │ │daily loss  │   │ challenger    │  │ symbol/tf    │  │ equity·signals·risk·audit   │
└───────┘ │exposure    │   └──────────────┘  └──────────────┘  └──────────────────────────┘
          │PDT·throttle│
          └────────────┘
```

## Data flow per bar (live)

1. `MarketDataProvider.fetch_latest` pulls the last N bars per symbol (Alpaca, yfinance, or synthetic).
2. `FeatureEngineer.transform` computes ~33 strictly causal features (returns, vol, RSI/MACD/Bollinger,
   ATR, session VWAP deviation, volume z-scores, Roll spread, Amihud illiquidity, fractionally
   differentiated price, rolling skew/kurtosis/autocorrelation, time-of-day encodings).
3. The champion `SignalModel` outputs P(up) for the triple-barrier label.
4. `RegimeDetector` (2-state Gaussian HMM on returns + realized vol) tags calm/volatile.
5. Optional `LLMAnalyst` (Claude, structured output) adds a bounded sentiment tilt (±30% of size).
6. `SignalPolicy` converts edge = 2p-1 into vol-targeted weights with a confidence dead-zone.
7. `RiskManager` enforces kill switch, daily loss limit, drawdown kill, per-name and gross caps,
   PDT guard and order throttles. Same object is used in backtests for behavioural parity.
8. `OrderManager` diffs target vs. broker positions, applies hysteresis, submits idempotent orders,
   books round-trips and persists everything.
9. Ten minutes before the close the engine flattens (day-trading constraint).

## Training & evaluation

* **Labels**: triple barrier (vol-scaled profit-take / stop-loss / time), with uniqueness-based
  sample weights to de-emphasise overlapping outcomes.
* **CV**: `PurgedKFold` purges training samples whose label window overlaps the test fold and adds
  an embargo. Walk-forward evaluation is anchored and chronological.
* **HPO**: Optuna TPE over LightGBM (or TCN) hyper-parameters, objective = economic edge capture
  minus a calibration penalty.
* **Statistics**: Sharpe, Sortino, max drawdown, Calmar, Probabilistic Sharpe Ratio and Deflated
  Sharpe Ratio (penalises the number of trials run), hit rate, profit factor, expectancy.
* **Models**: LightGBM (default), stacked GBM ensemble, PyTorch TCN (optional extra `deep`).

## Self-training (autopilot)

Each cycle (nightly or on demand): sync data → PSI feature drift + live decay → candidates
(fresh retrain, HPO challenger, drift-pruned feature set, ensemble, human-queued experiments,
Claude-proposed experiments) → identical walk-forward harness → promotion gates (min OOS trades,
DSR ≥ threshold, Sharpe gain ≥ margin, drawdown not worse, weekly promotion cap, optional manual
approval). Promotion hot-swaps the champion in the running engine; the previous champion is kept
for one-click rollback.

## Safety model

* Paper is the default. Live requires `ALPHAFORGE_TRADING_MODE=live` **and**
  `ALPHAFORGE_LIVE_TRADING_ACK=I_UNDERSTAND_THE_RISK`.
* Kill switch (manual or drawdown) flattens, cancels and refuses all orders until an admin resets it.
* Every admin action is written to the audit log; every risk decision to risk_events.
