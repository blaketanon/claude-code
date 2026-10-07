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
| `crosstab.csv` | Ocrolus tag by GustavoAI tag, every combination with its count |

Both scripts use only the Python standard library.

## Where the data comes from

- Every run starts from the Ocrolus `enriched_txns` response stored in the
  BrokerBox database (`Ocrolus_Response`, one row per book). GustavoAI keeps
  Ocrolus's per-transaction flags on `transactions[].hints` and collapses them
  to `hints.ocr_tag`. Only Ocrolus's `fintech_mca` flag counts as MCA in the
  comparison, the same rule the app uses; `bank_cash_advance` is a separate
  Ocrolus tag and shows up as its own row in the cross-tab.
- `GET /api/runs` lists the newest 100 runs (no paging). `GET /api/runs/{id}`
  returns `transactions[]`, `feedback[]` and `report{labels[], advances[],
  ocrComparison{counts, verdicts}, llm}`. Verdicts are `agree`, `ours_only`,
  `ocr_only`, `possible` (GustavoAI said "possible MCA", whatever Ocrolus
  said) and `neither`.
- `GET /api/review?runId=` gives the same labels as the console's Review
  page, saved beside each run under `data/review/`.
- `GET /api/review/stats` and `GET /api/usage` are computed by the app over
  every run in its Postgres database, so they are not limited by the 100-run
  list; the report prints them alongside the per-run aggregation. Runs older
  than the newest 100 can be pulled by id with `fetch_runs.py --ids ids.txt`.
- Reviewer feedback (`feedback[]`, from the console's transaction drawer)
  carries `correct` and, when wrong, `corrected_tag`. The report uses it to
  score both systems against the reviewer's call where one exists.
- The run data itself lives in the GustavoAI Postgres database on AWS
  (`runs`, `transactions`, `transaction_labels`, `advances`, `label_feedback`,
  `ai_calls`); the API is the supported way to read it.

The GustavoAI host is not reachable from a Claude cloud session unless
`gus.geroai.fund` is added to the environment's allowed domains, so the
scripts are written to run anywhere with network access and to work offline
from the saved `data/` directory afterwards.
