/**
 * On-demand "GustavoAI vs Ocrolus" report over the application database.
 *
 * Two questions, answered over whichever runs the filters select:
 *   1. MCA tagging: where do GustavoAI's MCA labels and Ocrolus's fintech_mca
 *      flag agree, and where not, per transaction, per funder, per run.
 *   2. Revenue tagging: on the deposit side, which credits each system takes
 *      out of revenue (advance and loan deposits, transfers, returns), and how
 *      much monthly revenue moves as a result.
 *
 * Ocrolus's per-line flags are read from transactions.ocr_flags (jsonb), the
 * collapsed tag from transactions.ocr_tag, and only fintech_mca counts as MCA,
 * the same rule src/compare.ts applies when it writes the verdicts.
 */
import { getDb } from "./store.js";

export interface CompareQuery {
  opportunityId?: string;
  since?: string;        // run created on/after (ISO date)
  until?: string;        // run created before (ISO date, exclusive)
  funder?: string;       // substring on our funder / counterparty or Ocrolus counterparty
  scope?: "latest" | "all"; // latest finished run per opportunity (default) or every finished run
  limit?: number;        // rows in the disagreement list, default 500, max 5000
}

/** Ocrolus flags that take a credit out of revenue, grouped by why. */
const OCR_FINANCING = ["fintech_mca", "fintech_loan", "bank_cash_advance", "bank_loan", "equipment_lease", "factoring", "sba", "other_loan", "credit_builder", "debt_consolidator"];
const OCR_TRANSFER = ["internal_transfer", "unreconciled_internal_transfer"];
const OCR_RETURN = ["returned_item", "reversed", "nsf_returned_or_not_paid", "nsf", "stop_payment"];

const flagAny = (col: string, flags: string[]) => "(" + flags.map((f) => `coalesce((${col}->>'${f}')::boolean, false)`).join(" OR ") + ")";

/** pg returns count()/sum()/numeric as strings; turn those into numbers but leave ids and text alone. */
const ID_LIKE = /(^|_)(id|month|label|tag|funder|counterparty|source|description|account|reason|note|reviewer|verdict|kind|class|type)$/;
function num(o: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(o).map(([k, v]) => [k, !ID_LIKE.test(k) && typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v) ? Number(v) : v]));
}

export async function compareReport(q: CompareQuery = {}): Promise<Record<string, unknown>> {
  const db = getDb();
  const params: unknown[] = [];
  const runWhere: string[] = ["r.status = 'done'"];
  if (q.opportunityId) { params.push(q.opportunityId); runWhere.push(`r.opportunity_id = $${params.length}`); }
  if (q.since) { params.push(q.since); runWhere.push(`r.created_at >= $${params.length}`); }
  if (q.until) { params.push(q.until); runWhere.push(`r.created_at < $${params.length}`); }
  const scope = q.scope === "all" ? "all" : "latest";
  // The run set. "latest" keeps one run per opportunity (newest finished); runs
  // without an opportunity id are kept individually.
  const runsCte = scope === "latest"
    ? `SELECT DISTINCT ON (coalesce(r.opportunity_id, r.id)) r.id, r.created_at, r.opportunity_id, r.ocrolus_response_id, r.source_label, r.model_requested, r.transaction_count
       FROM runs r WHERE ${runWhere.join(" AND ")} ORDER BY coalesce(r.opportunity_id, r.id), r.created_at DESC`
    : `SELECT r.id, r.created_at, r.opportunity_id, r.ocrolus_response_id, r.source_label, r.model_requested, r.transaction_count FROM runs r WHERE ${runWhere.join(" AND ")}`;

  const funderFilter: string[] = [];
  if (q.funder) {
    params.push(`%${q.funder}%`);
    funderFilter.push(`(l.funder ILIKE $${params.length} OR l.counterparty ILIKE $${params.length} OR t.ocr_counterparty ILIKE $${params.length} OR (t.ocr_flags->>'fintech_mca_source') ILIKE $${params.length})`);
  }
  const ff = funderFilter.length ? " AND " + funderFilter.join(" AND ") : "";

  // One row per transaction in the run set, with both systems' view of it.
  const linesCte = `
    SELECT s.id AS run_id, s.created_at AS run_at, s.opportunity_id, s.ocrolus_response_id, s.source_label,
           t.txn_id, t.txn_date, to_char(t.txn_date, 'YYYY-MM') AS month, t.description, t.amount, t.type, t.account,
           l.tag, l.is_positive AS ours_mca, l.funder, l.counterparty, l.source, l.rules_tag, l.reason, l.verdict,
           t.ocr_counterparty, coalesce(t.ocr_tag, '') AS ocr_tag, t.ocr_mca, t.ocr_flags->>'fintech_mca_source' AS ocr_source,
           ${flagAny("t.ocr_flags", OCR_FINANCING)} AS ocr_financing,
           ${flagAny("t.ocr_flags", OCR_TRANSFER)} AS ocr_transfer,
           ${flagAny("t.ocr_flags", OCR_RETURN)} AS ocr_return,
           (l.tag IN ('mca_deposit', 'non_cash_advance_deposit')) AS ours_financing_deposit,
           (l.tag = 'mca_return') AS ours_return,
           f.correct AS reviewed_correct, f.corrected_tag, f.reviewer, f.note AS review_note
    FROM (${runsCte}) s
    JOIN transactions t ON t.run_id = s.id
    LEFT JOIN transaction_labels l ON l.run_id = t.run_id AND l.txn_id = t.txn_id AND l.category = 'mca'
    LEFT JOIN LATERAL (
      SELECT * FROM label_feedback f WHERE f.run_id = t.run_id AND f.txn_id = t.txn_id AND f.category = 'mca' ORDER BY f.created_at DESC LIMIT 1
    ) f ON true
    WHERE true${ff}`;

  const withLines = `WITH lines AS (${linesCte})`;

  const runs = (await db.query(`WITH s AS (${runsCte}) SELECT count(*) AS runs, count(DISTINCT opportunity_id) AS opportunities, coalesce(sum(transaction_count),0) AS transactions, min(created_at) AS first_run, max(created_at) AS last_run FROM s`, params)).rows[0];

  // ---- MCA tagging ----
  const mca = (await db.query(`${withLines}
    SELECT count(*) AS lines,
           count(*) FILTER (WHERE ours_mca) AS ours_mca,
           count(*) FILTER (WHERE ocr_mca) AS ocr_mca,
           count(*) FILTER (WHERE ours_mca AND ocr_mca) AS agree,
           count(*) FILTER (WHERE ours_mca AND NOT ocr_mca) AS ours_only,
           count(*) FILTER (WHERE NOT coalesce(ours_mca,false) AND ocr_mca AND coalesce(tag,'') <> 'possible_mca_pull') AS ocr_only,
           count(*) FILTER (WHERE tag = 'possible_mca_pull') AS possible,
           count(*) FILTER (WHERE tag = 'possible_mca_pull' AND ocr_mca) AS possible_ocr_mca,
           count(*) FILTER (WHERE NOT coalesce(ours_mca,false) AND NOT ocr_mca) AS neither,
           coalesce(sum(amount) FILTER (WHERE ours_mca AND type='debit'),0) AS ours_pull_total,
           coalesce(sum(amount) FILTER (WHERE ocr_mca AND type='debit'),0) AS ocr_pull_total,
           coalesce(sum(amount) FILTER (WHERE ours_mca AND ocr_mca AND type='debit'),0) AS agree_pull_total,
           count(*) FILTER (WHERE rules_tag IS NOT NULL AND rules_tag <> tag) AS claude_changed
    FROM lines`, params)).rows[0];

  const verdicts = (await db.query(`${withLines} SELECT coalesce(verdict, 'n/a') AS verdict, count(*) AS n, coalesce(sum(amount),0) AS amount FROM lines GROUP BY 1 ORDER BY 2 DESC`, params)).rows;

  const crosstab = (await db.query(`${withLines} SELECT CASE WHEN ocr_tag = '' THEN '(none)' ELSE ocr_tag END AS ocr_tag, coalesce(tag, 'untagged') AS our_tag, count(*) AS n, coalesce(sum(amount),0) AS amount FROM lines GROUP BY 1, 2 ORDER BY 3 DESC`, params)).rows;

  const funders = (await db.query(`${withLines}
    SELECT coalesce(nullif(funder, 'No Advance'), counterparty, ocr_source, ocr_counterparty, '(none)') AS funder,
           count(*) AS n,
           count(*) FILTER (WHERE ours_mca) AS ours_mca, count(*) FILTER (WHERE ocr_mca) AS ocr_mca,
           count(*) FILTER (WHERE ours_mca AND ocr_mca) AS agree,
           count(*) FILTER (WHERE ours_mca AND NOT ocr_mca) AS ours_only,
           count(*) FILTER (WHERE NOT coalesce(ours_mca,false) AND ocr_mca AND coalesce(tag,'') <> 'possible_mca_pull') AS ocr_only,
           count(*) FILTER (WHERE tag = 'possible_mca_pull') AS possible,
           coalesce(sum(amount) FILTER (WHERE type='debit' AND (ours_mca OR ocr_mca)),0) AS pull_total,
           count(DISTINCT run_id) AS runs,
           count(*) FILTER (WHERE reviewed_correct = false) AS wrong
    FROM lines WHERE ours_mca OR ocr_mca OR tag = 'possible_mca_pull'
    GROUP BY 1 ORDER BY 2 DESC LIMIT 150`, params)).rows;

  const perRun = (await db.query(`${withLines}
    SELECT run_id, run_at, opportunity_id, ocrolus_response_id, source_label, count(*) AS lines,
           count(*) FILTER (WHERE ours_mca) AS ours_mca, count(*) FILTER (WHERE ocr_mca) AS ocr_mca,
           count(*) FILTER (WHERE ours_mca AND ocr_mca) AS agree,
           count(*) FILTER (WHERE ours_mca AND NOT ocr_mca) AS ours_only,
           count(*) FILTER (WHERE NOT coalesce(ours_mca,false) AND ocr_mca AND coalesce(tag,'') <> 'possible_mca_pull') AS ocr_only,
           count(*) FILTER (WHERE tag = 'possible_mca_pull') AS possible,
           coalesce(sum(amount) FILTER (WHERE type='credit'),0) AS credits,
           coalesce(sum(amount) FILTER (WHERE type='credit' AND NOT ocr_financing AND NOT ocr_transfer AND NOT ocr_return),0) AS ocr_revenue,
           coalesce(sum(amount) FILTER (WHERE type='credit' AND NOT ours_financing_deposit AND NOT ours_return AND NOT ocr_transfer AND NOT ocr_return),0) AS ours_revenue,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL) AS reviewed,
           count(*) FILTER (WHERE reviewed_correct = false) AS wrong
    FROM lines GROUP BY 1,2,3,4,5 ORDER BY run_at DESC LIMIT 500`, params)).rows;

  // ---- Revenue tagging (credits only) ----
  const revenue = (await db.query(`${withLines}
    SELECT count(*) AS deposits, coalesce(sum(amount),0) AS credits,
           coalesce(sum(amount) FILTER (WHERE ocr_financing),0) AS ocr_financing_amt, count(*) FILTER (WHERE ocr_financing) AS ocr_financing_n,
           coalesce(sum(amount) FILTER (WHERE ocr_transfer),0) AS transfer_amt, count(*) FILTER (WHERE ocr_transfer) AS transfer_n,
           coalesce(sum(amount) FILTER (WHERE ocr_return),0) AS return_amt, count(*) FILTER (WHERE ocr_return) AS return_n,
           coalesce(sum(amount) FILTER (WHERE ours_financing_deposit),0) AS ours_financing_amt, count(*) FILTER (WHERE ours_financing_deposit) AS ours_financing_n,
           coalesce(sum(amount) FILTER (WHERE ours_return),0) AS ours_return_amt, count(*) FILTER (WHERE ours_return) AS ours_return_n,
           coalesce(sum(amount) FILTER (WHERE NOT ocr_financing AND NOT ocr_transfer AND NOT ocr_return),0) AS ocr_revenue,
           coalesce(sum(amount) FILTER (WHERE NOT ours_financing_deposit AND NOT ours_return AND NOT ocr_transfer AND NOT ocr_return),0) AS ours_revenue,
           count(*) FILTER (WHERE ours_financing_deposit AND NOT ocr_financing) AS ours_only_financing_n,
           coalesce(sum(amount) FILTER (WHERE ours_financing_deposit AND NOT ocr_financing),0) AS ours_only_financing_amt,
           count(*) FILTER (WHERE ocr_financing AND NOT ours_financing_deposit) AS ocr_only_financing_n,
           coalesce(sum(amount) FILTER (WHERE ocr_financing AND NOT ours_financing_deposit),0) AS ocr_only_financing_amt,
           count(*) FILTER (WHERE ocr_financing AND ours_financing_deposit) AS both_financing_n
    FROM lines WHERE type = 'credit'`, params)).rows[0];

  const revenueByMonth = (await db.query(`${withLines}
    SELECT month, count(DISTINCT run_id) AS runs, count(*) AS deposits, coalesce(sum(amount),0) AS credits,
           coalesce(sum(amount) FILTER (WHERE NOT ocr_financing AND NOT ocr_transfer AND NOT ocr_return),0) AS ocr_revenue,
           coalesce(sum(amount) FILTER (WHERE NOT ours_financing_deposit AND NOT ours_return AND NOT ocr_transfer AND NOT ocr_return),0) AS ours_revenue,
           count(*) FILTER (WHERE ours_financing_deposit <> ocr_financing) AS differing
    FROM lines WHERE type = 'credit' GROUP BY 1 ORDER BY 1 DESC LIMIT 36`, params)).rows;

  const depositCrosstab = (await db.query(`${withLines}
    SELECT CASE WHEN ocr_financing THEN 'financing' WHEN ocr_transfer THEN 'transfer' WHEN ocr_return THEN 'return' WHEN ocr_tag <> '' THEN ocr_tag ELSE 'revenue' END AS ocr_class,
           CASE WHEN ours_financing_deposit THEN tag WHEN ours_return THEN 'mca_return' ELSE coalesce(tag, 'untagged') END AS our_tag,
           count(*) AS n, coalesce(sum(amount),0) AS amount
    FROM lines WHERE type = 'credit' GROUP BY 1, 2 ORDER BY 3 DESC`, params)).rows;

  // ---- Reviewer adjudication (latest feedback per line) ----
  const review = (await db.query(`${withLines}
    SELECT count(*) FILTER (WHERE reviewed_correct IS NOT NULL) AS reviewed,
           count(*) FILTER (WHERE reviewed_correct = true) AS ours_confirmed,
           count(*) FILTER (WHERE reviewed_correct = false) AS ours_corrected,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL AND (CASE WHEN reviewed_correct THEN ours_mca ELSE corrected_tag IN ('mca_pull','mca_deposit','mca_return') END) = coalesce(ours_mca,false)) AS ours_mca_right,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL AND (CASE WHEN reviewed_correct THEN ours_mca ELSE corrected_tag IN ('mca_pull','mca_deposit','mca_return') END) = ocr_mca) AS ocr_mca_right,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL AND coalesce(ours_mca,false) <> ocr_mca) AS disputed_reviewed,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL AND coalesce(ours_mca,false) <> ocr_mca AND (CASE WHEN reviewed_correct THEN ours_mca ELSE corrected_tag IN ('mca_pull','mca_deposit','mca_return') END) = coalesce(ours_mca,false)) AS disputed_ours_right,
           count(*) FILTER (WHERE reviewed_correct IS NOT NULL AND coalesce(ours_mca,false) <> ocr_mca AND (CASE WHEN reviewed_correct THEN ours_mca ELSE corrected_tag IN ('mca_pull','mca_deposit','mca_return') END) = ocr_mca) AS disputed_ocr_right
    FROM lines`, params)).rows[0];

  // ---- Disagreement list ----
  params.push(Math.min(q.limit ?? 500, 5000));
  const disagreements = (await db.query(`${withLines}
    SELECT run_id, run_at, opportunity_id, source_label, txn_id, txn_date, description, amount, type, account,
           tag, funder, counterparty, source, reason, verdict, ocr_tag, ocr_counterparty, ocr_source, ocr_mca, ocr_financing, ocr_transfer, ocr_return,
           ours_financing_deposit, reviewed_correct, corrected_tag, reviewer, review_note,
           CASE WHEN type = 'credit' AND ours_financing_deposit <> ocr_financing THEN 'revenue'
                WHEN tag = 'possible_mca_pull' THEN 'possible'
                WHEN ours_mca AND NOT ocr_mca THEN 'ours_only'
                WHEN NOT coalesce(ours_mca,false) AND ocr_mca THEN 'ocr_only' END AS kind
    FROM lines
    WHERE coalesce(ours_mca,false) <> ocr_mca OR tag = 'possible_mca_pull' OR (type = 'credit' AND ours_financing_deposit <> ocr_financing)
    ORDER BY run_at DESC, amount DESC LIMIT $${params.length}`, params)).rows;

  return {
    generatedAt: new Date().toISOString(),
    query: { ...q, scope },
    runs: num(runs),
    mca: num(mca),
    verdicts: verdicts.map(num),
    crosstab: crosstab.map(num),
    funders: funders.map(num),
    perRun: perRun.map(num),
    revenue: num(revenue),
    revenueByMonth: revenueByMonth.map(num),
    depositCrosstab: depositCrosstab.map(num),
    review: num(review),
    disagreements: disagreements.map(num),
  };
}
