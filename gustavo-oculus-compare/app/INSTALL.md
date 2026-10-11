# Compare page for GustavoAI

A seventh console view, **Compare** (`/compare.html`), that builds a GustavoAI
vs Ocrolus report on demand from the application database, focused on MCA
tagging and on revenue tagging (which deposits each system takes out of
revenue). It needs `DATABASE_URL` like the Review page.

Files in this folder, relative to the `mca-detector` repo:

| File | Goes to | What it is |
|---|---|---|
| `src/compareReport.ts` | `src/compareReport.ts` | `compareReport(q)`: the SQL over `runs`, `transactions`, `transaction_labels` and `label_feedback`, returning the report JSON. Uses `getDb()` from `src/store.ts`. |
| `public/compare.html` | `public/compare.html` | The page. Uses `common.css` / `common.js` like the other views (`$`, `api`, `esc`, `usd`, `VERDICT_LABEL`, `OCR_LABEL`, `loadHealth`, `openRunModal`). |

Three small edits wire it in.

## 1. Route (`src/server.ts`)

Next to the other imports:

```ts
import { compareReport } from "./compareReport.js";
```

Next to `/api/review/stats`:

```ts
  /** GustavoAI vs Ocrolus report over the selected runs: MCA tagging, revenue tagging, reviewer adjudication, disagreements. */
  app.get<{ Querystring: { opportunityId?: string; since?: string; until?: string; funder?: string; scope?: "latest" | "all"; limit?: string } }>("/api/compare", async (req, reply) => {
    if (!store.dbConfigured()) return reply.code(503).send({ error: "needs the application database" });
    const { opportunityId, since, until, funder, scope, limit } = req.query;
    return compareReport({ opportunityId, since, until, funder, scope: scope === "all" ? "all" : "latest", limit: limit ? Number(limit) : undefined });
  });
```

## 2. Navigation (`public/common.js`)

Add the page to `NAV`, between Review and Usage:

```js
const NAV = [["/", "Test"], ["/runs.html", "Runs"], ["/review.html", "Review"], ["/compare.html", "Compare"], ["/usage.html", "Usage"], ["/admin.html", "Admin"], ["/docs.html", "API docs"]];
```

## 3. API docs (`src/openapi.ts`)

Under `paths`, next to `/api/review/stats`:

```ts
    "/api/compare": { get: { tags: ["review"], summary: "GustavoAI vs Ocrolus report: MCA tagging agreement, revenue tagging on deposits, reviewer adjudication, per-funder and per-run breakdowns, disagreement list", parameters: [{ name: "scope", in: "query", description: "latest (default: newest finished run per opportunity) | all" }, { name: "opportunityId", in: "query" }, { name: "funder", in: "query", description: "substring on our funder/counterparty or Ocrolus counterparty/source" }, { name: "since", in: "query", description: "runs created on/after (ISO date)" }, { name: "until", in: "query", description: "runs created before (ISO date)" }, { name: "limit", in: "query", description: "disagreement rows, default 500, max 5000" }], responses: { 200: { description: "{ generatedAt, query, runs, mca, verdicts, crosstab, funders, perRun, revenue, revenueByMonth, depositCrosstab, review, disagreements }" }, 503: { description: "application database not configured" } } } },
```

Then `npx vitest run`, `npm run bundle && eb deploy GustavoAI --timeout 20`, and open https://gus.geroai.fund/compare.html.

## What the report says

- **MCA tagging.** Lines each system calls MCA (Ocrolus = `fintech_mca`),
  the agree / GustavoAI-only / Ocrolus-only / possible split, pull dollars
  per system, a confusion matrix, and the Ocrolus-tag by GustavoAI-tag
  cross-tab so `bank_cash_advance`, loans and payroll show where they landed.
- **By funder.** For every funder or counterparty either system associates
  with MCA activity: lines, agreement, pulls, and how often a reviewer marked
  GustavoAI wrong.
- **Revenue tagging.** Deposits only. Revenue = gross credits minus financing
  deposits, internal transfers and returned credits. Transfers and returns
  come from Ocrolus's flags for both systems (GustavoAI does not tag them
  yet), so the difference between "revenue per Ocrolus" and "revenue per
  GustavoAI" is exactly the deposits the two disagree on as advances. Shown
  as totals, as a deposit-class cross-tab, and by statement month.
- **When a reviewer looked.** Using the latest feedback per line, how often
  each system matched the reviewer's MCA / not-MCA call, overall and on the
  lines where the two systems disagreed.
- **Per run** and a filterable **disagreement list** (kind, reviewed or not,
  text), each row opening the Test view on that transaction so it can be
  marked. Disagreements export to CSV; the whole report to JSON.

The filters go into the URL hash, so a built report can be linked.

## Scope

`scope=latest` (default) keeps one run per opportunity, the newest finished
one, so re-runs do not double count; runs without an opportunity id are kept
individually. `scope=all` compares every finished run. Both ignore running,
failed and cancelled runs.

## Validation

`compareReport.ts` was compiled and run against a local Postgres 16 loaded
with `sql/001_init.sql` and `sql/002_webhooks.sql` and a small fixture (three
finished runs across two opportunities, one re-run, reviewer feedback on three
lines). The page was rendered in Chromium against that output with stub
`common.js` helpers. It has not yet been run against the live GustavoAI
database.
