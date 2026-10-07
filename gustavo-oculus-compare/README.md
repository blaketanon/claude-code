# GustavoAI vs Ocrolus comparison

Two scripts that pull every GustavoAI run and compare what GustavoAI found in
the bank statements with what Ocrolus tagged for the same transactions.

GustavoAI (the `mca-detector` service at https://gus.geroai.fund) takes the
Ocrolus enriched transactions for a deal, re-tags each transaction (MCA pull,
MCA deposit, return, non-cash-advance pull, possible MCA, excluded recurring,
other), groups them into advances, and records an `ocrComparison` block per
run with where it agrees and disagrees with Ocrolus's MCA tags. Reviewer
feedback on individual transactions is stored with the run.

## Run it

```bash
# 1. pull every finished run plus the review statistics (no auth today)
python3 fetch_runs.py --base https://gus.geroai.fund --out data

# 2. aggregate and write the report
python3 compare.py --data data --out report
```

`report/report.md` is the write-up. Beside it:

| File | Contents |
|---|---|
| `summary.json` | headline totals, verdict mix, reviewer feedback tallies, confusion matrix |
| `per_run.csv` | one row per run: agree / GustavoAI-only / Ocrolus-only / possible counts, advances, cost |
| `per_funder.csv` | agreement by funder |
| `disagreements.csv` | every transaction where the two systems differ on MCA, with both tags and GustavoAI's reason |
| `advances.csv` | every advance GustavoAI reported (funder, type, frequency, pull, status, confidence) |
| `balances.csv` | credits, debits and net per run and account; running-balance reconciliation when the transactions carry balances |

Both scripts use only the Python standard library.

## Where the data comes from

- `GET /api/runs` lists runs; `GET /api/runs/{id}` returns `transactions[]`,
  `feedback[]` and `report{labels[], advances[], ocrComparison, llm}`.
- `GET /api/review/stats` gives the console's own cross-run review totals and
  is saved as `data/review_stats.json` for cross-checking.
- The run data itself lives in the GustavoAI Postgres database on AWS; the API
  is the supported way to read it.

The GustavoAI host is not reachable from a Claude cloud session unless
`gus.geroai.fund` is added to the environment's allowed domains, so the
scripts are written to run anywhere with network access and to work offline
from the saved `data/` directory afterwards.
