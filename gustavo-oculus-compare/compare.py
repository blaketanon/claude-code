#!/usr/bin/env python3
"""Compare what GustavoAI found in the bank statements with what Ocrolus
("Oculus") tagged for the same transactions, across every saved run.

Input: the directory written by fetch_runs.py (data/runs/*.json plus
runs_index.json and review_stats.json when present). Each run JSON is what
GET /api/runs/{id} returns: transactions[], feedback[] and report{labels[],
advances[], ocrComparison{counts, agree[], oursOnly[], ocrOnly[], possible[],
verdicts{}}}.

Output (in --out, default report/):
    summary.json        headline numbers
    per_run.csv         one row per run
    per_funder.csv      agreement by funder
    disagreements.csv   every transaction where GustavoAI and Ocrolus differ
    advances.csv        every advance GustavoAI reported
    balances.csv        per run / account balance reconciliation (when the
                        transactions carry balances)
    report.md           a readable write-up of the above
"""
import argparse
import csv
import glob
import json
import os
from collections import Counter, defaultdict

MCA_TAGS = {"mca_pull", "mca_deposit", "mca_return"}
PULL_TAGS = {"mca_pull", "possible_mca_pull", "non_cash_advance_pull"}


def load_runs(data_dir):
    runs = []
    for path in sorted(glob.glob(os.path.join(data_dir, "runs", "*.json"))):
        with open(path) as f:
            runs.append(json.load(f))
    return runs


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def txn_map(run):
    return {str(t.get("id")): t for t in run.get("transactions") or []}


def hints_of(t):
    h = t.get("hints")
    return h if isinstance(h, dict) else {}


def ocr_tag_of(t):
    """Ocrolus's collapsed tag for a transaction. GustavoAI writes it to
    transactions[].hints.ocr_tag (mca, bank_cash_advance, loan, bank_loan,
    equipment_lease, factoring, sba, other_loan, return, nsf_paid, payroll, tax,
    insurance, credit_card, merchant_service, internal_transfer, p2p, wire or
    empty)."""
    h = hints_of(t)
    tag = h.get("ocr_tag")
    if tag:
        return str(tag)
    if h.get("fintech_mca") is True:
        return "mca"
    return ""


def ocr_is_mca(t, verdicts, tid):
    """Only Ocrolus's fintech_mca flag counts as MCA, matching GustavoAI's own
    comparison. bank_cash_advance is a separate Ocrolus tag."""
    h = hints_of(t)
    if "fintech_mca" in h:
        return h.get("fintech_mca") is True
    if h.get("ocr_tag"):
        return h["ocr_tag"] == "mca"
    v = verdicts.get(tid)
    return v in ("agree", "ocr_only")


VERDICT_NAMES = {"oursOnly": "ours_only", "ocrOnly": "ocr_only"}


def verdict_of(v):
    if isinstance(v, dict):
        v = v.get("verdict") or v.get("kind") or ""
    v = str(v or "")
    return VERDICT_NAMES.get(v, v)


def analyse(runs):
    totals = Counter()
    verdict_mix = Counter()
    per_run = []
    per_funder = defaultdict(Counter)
    disagreements = []
    advances = []
    balances = []
    feedback_scores = Counter()
    tag_mix = Counter()
    ocr_tag_mix = Counter()
    confusion = Counter()  # (ours_mca, ocr_mca)
    crosstab = Counter()   # (ocrolus_tag, gustavo_tag)
    possible_split = Counter()  # possible_mca_pull by whether Ocrolus said MCA
    source_mix = Counter()  # rules vs claude, and how often claude changed the rules tag

    for run in runs:
        rid = run.get("id")
        report = run.get("report") or {}
        comp = report.get("ocrComparison") or {}
        counts = comp.get("counts") or {}
        labels = report.get("labels") or []
        label_by_id = {str(l.get("id")): l for l in labels}
        tmap = txn_map(run)
        verdicts = comp.get("verdicts") or {}
        src = run.get("source") or {}
        llm = report.get("llm") or {}
        cost = (llm.get("cost") or {}).get("totalUsd")

        for k in ("agree", "oursOnly", "ocrOnly", "possible", "ocrMca", "oursMca"):
            totals[k] += int(counts.get(k) or 0)
        totals["runs"] += 1
        totals["transactions"] += int(run.get("transactionCount") or len(tmap))
        if cost is not None:
            totals["cost_usd"] += float(cost)

        # transaction-level confusion matrix, from verdicts when present else from labels
        for tid, t in tmap.items():
            lab = label_by_id.get(tid) or {}
            ours_tag = lab.get("tag") or ""
            tag_mix[ours_tag or "untagged"] += 1
            otag = ocr_tag_of(t)
            if otag:
                ocr_tag_mix[otag] += 1
            ours_mca = ours_tag in MCA_TAGS
            ocr_mca = ocr_is_mca(t, verdicts, tid)
            confusion[(ours_mca, ocr_mca)] += 1
            crosstab[(otag or "(none)", ours_tag or "untagged")] += 1
            if ours_tag == "possible_mca_pull":
                possible_split["ocrolus_mca" if ocr_mca else "ocrolus_not_mca"] += 1
            if lab:
                source_mix[lab.get("source") or "unknown"] += 1
            v = verdict_of(verdicts.get(tid))
            if v:
                verdict_mix[v] += 1
            funder = lab.get("funder") or hints_of(t).get("counterparty") or ""
            if funder == "No Advance":
                funder = lab.get("counterparty") or hints_of(t).get("counterparty") or funder
            if funder:
                key = "agree" if ours_mca == ocr_mca else ("oursOnly" if ours_mca else "ocrOnly")
                per_funder[funder][key] += 1
                per_funder[funder]["n"] += 1
            if ours_mca != ocr_mca or ours_tag == "possible_mca_pull":
                disagreements.append({
                    "run": rid, "txn": tid, "date": t.get("date"), "amount": t.get("amount"),
                    "type": t.get("type"), "description": (t.get("description") or "")[:120],
                    "gustavo_tag": ours_tag, "gustavo_funder": funder, "gustavo_source": lab.get("source"),
                    "ocrolus_tag": otag, "ocrolus_mca": ocr_mca, "ocrolus_counterparty": hints_of(t).get("counterparty"),
                    "ocrolus_source": hints_of(t).get("fintech_mca_source"), "verdict": v,
                    "reason": (lab.get("reason") or "")[:200],
                })

        # human feedback: label_feedback rows {txn_id, correct, corrected_tag,
        # corrected_funder, reviewer, note}. Keep the latest per transaction.
        latest = {}
        for fb in run.get("feedback") or []:
            tid = str(fb.get("txn_id") or fb.get("txnId") or "")
            if tid and (tid not in latest or str(fb.get("created_at") or "") >= str(latest[tid].get("created_at") or "")):
                latest[tid] = fb
        for tid, fb in latest.items():
            ok = bool(fb.get("correct"))
            lab = label_by_id.get(tid) or {}
            t = tmap.get(tid) or {}
            ours_tag = lab.get("tag") or ""
            truth_tag = ours_tag if ok else (fb.get("corrected_tag") or "")
            truth_mca = truth_tag in MCA_TAGS
            ours_mca = ours_tag in MCA_TAGS
            ocr_mca = ocr_is_mca(t, verdicts, tid)
            feedback_scores["reviewed"] += 1
            feedback_scores["gustavo_correct" if ok else "gustavo_wrong"] += 1
            if truth_tag:
                feedback_scores["gustavo_mca_right" if ours_mca == truth_mca else "gustavo_mca_wrong"] += 1
                feedback_scores["ocrolus_mca_right" if ocr_mca == truth_mca else "ocrolus_mca_wrong"] += 1
            if ours_mca != ocr_mca and truth_tag:
                feedback_scores["disputed_reviewed"] += 1
                feedback_scores["disputed_gustavo_right" if ours_mca == truth_mca else "disputed_ocrolus_right"] += 1

        # advances
        for a in report.get("advances") or []:
            advances.append({
                "run": rid, "opportunity": src.get("opportunityId"), "responseId": src.get("responseId"),
                "funder": a.get("funder"), "financingType": a.get("financingType"), "frequency": a.get("frequency"),
                "pullAmount": a.get("pullAmount"), "dailyPayment": a.get("dailyPayment"), "status": a.get("status"),
                "advanceType": a.get("advanceType"), "advanceDate": a.get("advanceDate"), "advanceAmount": a.get("advanceAmount"),
                "lastPullDate": a.get("lastPullDate"), "paidOff": a.get("paidOff"), "confidence": a.get("confidence"),
                "needsReview": a.get("needsReview"), "pulls": len(a.get("pullTransactionIds") or []),
                "deposits": len(a.get("depositTransactionIds") or []), "account": a.get("account"),
            })

        # balance reconciliation per account when balances are present
        by_acct = defaultdict(list)
        for t in tmap.values():
            by_acct[t.get("account") or ""].append(t)
        for acct, txns in by_acct.items():
            bal_key = next((k for k in ("balance", "runningBalance", "running_balance", "endingBalance") if any(k in t for t in txns)), None)
            txns = sorted(txns, key=lambda t: (t.get("date") or "", str(t.get("id"))))
            credits = sum(num(t.get("amount")) or 0 for t in txns if (t.get("type") or "").lower() == "credit")
            debits = sum(num(t.get("amount")) or 0 for t in txns if (t.get("type") or "").lower() == "debit")
            row = {"run": rid, "account": acct, "transactions": len(txns), "credits": round(credits, 2), "debits": round(debits, 2),
                   "net": round(credits - debits, 2), "first_date": txns[0].get("date") if txns else "", "last_date": txns[-1].get("date") if txns else ""}
            if bal_key:
                first_b, last_b = num(txns[0].get(bal_key)), num(txns[-1].get(bal_key))
                if first_b is not None and last_b is not None:
                    first_amt = num(txns[0].get("amount")) or 0
                    first_sign = 1 if (txns[0].get("type") or "").lower() == "credit" else -1
                    opening = first_b - first_sign * first_amt
                    row.update({"balance_field": bal_key, "opening_balance": round(opening, 2), "closing_balance": round(last_b, 2),
                                "expected_closing": round(opening + credits - debits, 2),
                                "balance_gap": round(last_b - (opening + credits - debits), 2)})
            balances.append(row)

        n_adv = len(report.get("advances") or [])
        per_run.append({
            "run": rid, "createdAt": run.get("createdAt"), "status": run.get("status"), "source": src.get("label"),
            "opportunity": src.get("opportunityId"), "responseId": src.get("responseId"), "model": run.get("model"),
            "useLlm": run.get("useLlm"), "transactions": run.get("transactionCount") or len(tmap),
            "agree": counts.get("agree"), "oursOnly": counts.get("oursOnly"), "ocrOnly": counts.get("ocrOnly"),
            "possible": counts.get("possible"), "ocrMca": counts.get("ocrMca"), "oursMca": counts.get("oursMca"),
            "advances": n_adv, "advances_needing_review": sum(1 for a in report.get("advances") or [] if a.get("needsReview")),
            "feedback": len(run.get("feedback") or []), "cost_usd": cost, "durationMs": run.get("durationMs"),
        })

    return {
        "totals": totals, "verdict_mix": verdict_mix, "per_run": per_run, "per_funder": per_funder,
        "disagreements": disagreements, "advances": advances, "balances": balances,
        "feedback": feedback_scores, "tag_mix": tag_mix, "ocr_tag_mix": ocr_tag_mix, "confusion": confusion,
        "crosstab": crosstab, "possible_split": possible_split, "source_mix": source_mix,
    }


def pct(a, b):
    return f"{100.0 * a / b:.1f}%" if b else "n/a"


def write_csv(path, rows, fields=None):
    if not rows:
        open(path, "w").close()
        return
    fields = fields or list({k: None for r in rows for k in r}.keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_report(res, out, index, review_stats, usage=None):
    t = res["totals"]
    c = res["confusion"]
    tp, fp, fn, tn = c[(True, True)], c[(True, False)], c[(False, True)], c[(False, False)]
    fb = res["feedback"]
    lines = []
    lines.append("# GustavoAI vs Ocrolus: bank statement data comparison\n")
    lines.append("Every run below took the Ocrolus enriched transactions for one deal as input, re-tagged them with GustavoAI (rules + Claude), and recorded where the two disagree on MCA activity.\n")
    lines.append("## Coverage\n")
    lines.append("| Metric | Value |\n|---|---|")
    lines.append(f"| Runs listed by the API | {len(index)} |")
    lines.append(f"| Finished runs compared | {t['runs']} |")
    lines.append(f"| Transactions compared | {t['transactions']} |")
    lines.append(f"| Advances reported by GustavoAI | {len(res['advances'])} |")
    lines.append(f"| Claude cost across runs | ${t['cost_usd']:.2f} |\n")
    lines.append("## Transaction tagging: agreement with Ocrolus\n")
    lines.append("Counts come from each run's own `ocrComparison` block.\n")
    lines.append("| Verdict | Transactions |\n|---|---|")
    for k, label in (("agree", "Both tagged MCA (agree)"), ("oursOnly", "GustavoAI MCA, Ocrolus not"), ("ocrOnly", "Ocrolus MCA, GustavoAI not"), ("possible", "GustavoAI 'possible MCA' (needs clarification)")):
        lines.append(f"| {label} | {t[k]} |")
    lines.append(f"| MCA transactions per Ocrolus | {t['ocrMca']} |")
    lines.append(f"| MCA transactions per GustavoAI | {t['oursMca']} |\n")
    denom = t["agree"] + t["oursOnly"] + t["ocrOnly"]
    lines.append(f"- Overlap on MCA-tagged transactions (agree / all MCA-tagged by either): **{pct(t['agree'], denom)}**")
    lines.append(f"- Share of Ocrolus MCA tags that GustavoAI also tagged MCA: **{pct(t['agree'], t['agree'] + t['ocrOnly'])}**")
    lines.append(f"- Share of GustavoAI MCA tags that Ocrolus also tagged MCA: **{pct(t['agree'], t['agree'] + t['oursOnly'])}**\n")
    lines.append("Transaction-level confusion matrix (recomputed from labels and verdicts):\n")
    lines.append("| | Ocrolus: MCA | Ocrolus: not MCA |\n|---|---|---|")
    lines.append(f"| GustavoAI: MCA | {tp} | {fp} |")
    lines.append(f"| GustavoAI: not MCA | {fn} | {tn} |\n")
    if res["verdict_mix"]:
        lines.append("Verdict mix (every transaction): " + ", ".join(f"{k}: {v}" for k, v in res["verdict_mix"].most_common()) + "\n")
    if res["source_mix"]:
        lines.append("Final tag decided by: " + ", ".join(f"{k}: {v}" for k, v in res["source_mix"].most_common()) + "\n")
    lines.append("### Ocrolus tag versus GustavoAI tag\n")
    lines.append("Rows are Ocrolus's collapsed tag (only `mca` is the fintech_mca flag), columns GustavoAI's final tag.\n")
    ct = res["crosstab"]
    gtags = [k for k, _ in Counter({g: n for (_, g), n in ct.items()}).most_common()]
    otags = [k for k, _ in Counter({o: n for (o, _), n in ct.items()}).most_common()]
    lines.append("| Ocrolus \\ GustavoAI | " + " | ".join(gtags) + " | total |")
    lines.append("|---|" + "---|" * (len(gtags) + 1))
    for o in otags:
        row = [ct[(o, g)] for g in gtags]
        lines.append(f"| {o} | " + " | ".join(str(x) for x in row) + f" | {sum(row)} |")
    lines.append("")
    lines.append("## Who was right when a human looked\n")
    if fb["reviewed"]:
        lines.append(f"- Transactions with reviewer feedback: {fb['reviewed']} (GustavoAI tag confirmed {fb['gustavo_correct']}, corrected {fb['gustavo_wrong']})")
        lines.append(f"- Against the reviewer's MCA / not-MCA call: GustavoAI right {fb['gustavo_mca_right']}, wrong {fb['gustavo_mca_wrong']}; Ocrolus right {fb['ocrolus_mca_right']}, wrong {fb['ocrolus_mca_wrong']}")
        lines.append(f"- Of the reviewed transactions where the two systems disagreed ({fb['disputed_reviewed']}): GustavoAI right {fb['disputed_gustavo_right']}, Ocrolus right {fb['disputed_ocrolus_right']}\n")
    else:
        lines.append("- No reviewer feedback recorded yet, so disagreements cannot be adjudicated from the data alone.\n")
    if review_stats:
        rt = review_stats.get("totals") or {}
        lines.append("### The app's own cross-run review statistics (all runs in Postgres)\n")
        lines.append("| Metric | Value |\n|---|---|")
        for k in ("runs", "labels", "ours_mca", "ocr_mca", "reviewed", "correct", "wrong"):
            lines.append(f"| {k} | {rt.get(k)} |")
        lines.append(f"| Claude changed the rules tag | {review_stats.get('claudeChanged')} |")
        vs = review_stats.get("verdicts") or []
        if vs:
            lines.append("\nVerdicts: " + ", ".join(f"{v.get('verdict')}: {v.get('n')}" for v in vs))
        fs = review_stats.get("funders") or []
        if fs:
            lines.append("\n| Funder (app stats) | n | GustavoAI MCA | Ocrolus MCA | agree | GustavoAI only | Ocrolus only | reviewer said wrong |\n|---|---|---|---|---|---|---|---|")
            for f in fs[:30]:
                lines.append(f"| {f.get('funder')} | {f.get('n')} | {f.get('ours_mca')} | {f.get('ocr_mca')} | {f.get('agree')} | {f.get('ours_only')} | {f.get('ocr_only')} | {f.get('wrong')} |")
        lines.append("")
    if usage and usage.get("total"):
        u = usage["total"]
        lines.append(f"Usage across all runs: {u.get('runs')} runs, {u.get('llmRuns')} with Claude, ${float(u.get('costUsd') or 0):.2f} total.\n")
    lines.append("## GustavoAI tag mix\n")
    lines.append("| GustavoAI tag | Transactions |\n|---|---|")
    for k, v in res["tag_mix"].most_common():
        lines.append(f"| {k} | {v} |")
    if res["ocr_tag_mix"]:
        lines.append("\n| Ocrolus tag | Transactions |\n|---|---|")
        for k, v in res["ocr_tag_mix"].most_common(30):
            lines.append(f"| {k} | {v} |")
    lines.append("\n## Agreement by funder\n")
    lines.append("| Funder | Transactions | Agree | GustavoAI only | Ocrolus only |\n|---|---|---|---|---|")
    for funder, cnt in sorted(res["per_funder"].items(), key=lambda kv: -kv[1]["n"])[:40]:
        lines.append(f"| {funder} | {cnt['n']} | {cnt['agree']} | {cnt['oursOnly']} | {cnt['ocrOnly']} |")
    lines.append("\n## Advances\n")
    adv = res["advances"]
    st = Counter(a["status"] for a in adv)
    ft = Counter(a["financingType"] for a in adv)
    lines.append("Status: " + ", ".join(f"{k}: {v}" for k, v in st.most_common()))
    lines.append("\nFinancing type: " + ", ".join(f"{k}: {v}" for k, v in ft.most_common()))
    lines.append(f"\nNeeding review: {sum(1 for a in adv if a['needsReview'])} of {len(adv)}\n")
    bal = [b for b in res["balances"] if "balance_gap" in b]
    lines.append("## Balances\n")
    if bal:
        off = [b for b in bal if abs(b["balance_gap"]) > 0.01]
        lines.append(f"- Accounts with running balances: {len(bal)}; reconciled within $0.01: {len(bal) - len(off)}; off: {len(off)}")
        for b in off[:20]:
            lines.append(f"  - run {b['run']} account {b['account'] or '(default)'}: gap ${b['balance_gap']:.2f} (closing {b['closing_balance']}, expected {b['expected_closing']})")
    else:
        lines.append("- The run transactions carry no running balance field, so balances could not be reconciled from the GustavoAI data. Totals of credits and debits per account are in balances.csv.")
    lines.append("\nFiles: per_run.csv, per_funder.csv, disagreements.csv, advances.csv, balances.csv, crosstab.csv, summary.json\n")
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="report")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    runs = load_runs(args.data)
    if not runs:
        raise SystemExit(f"no run files under {args.data}/runs; run fetch_runs.py first")
    index = []
    p = os.path.join(args.data, "runs_index.json")
    if os.path.exists(p):
        index = json.load(open(p))
    review_stats = usage = None
    p = os.path.join(args.data, "review_stats.json")
    if os.path.exists(p):
        review_stats = json.load(open(p))
    p = os.path.join(args.data, "usage.json")
    if os.path.exists(p):
        usage = json.load(open(p))
    res = analyse(runs)
    write_csv(os.path.join(args.out, "per_run.csv"), res["per_run"])
    write_csv(os.path.join(args.out, "per_funder.csv"), [{"funder": k, **v} for k, v in res["per_funder"].items()], ["funder", "n", "agree", "oursOnly", "ocrOnly"])
    write_csv(os.path.join(args.out, "disagreements.csv"), res["disagreements"])
    write_csv(os.path.join(args.out, "advances.csv"), res["advances"])
    write_csv(os.path.join(args.out, "balances.csv"), res["balances"])
    summary = {
        "totals": dict(res["totals"]), "verdict_mix": dict(res["verdict_mix"]), "feedback": dict(res["feedback"]),
        "tag_mix": dict(res["tag_mix"]), "ocrolus_tag_mix": dict(res["ocr_tag_mix"]), "possible_split": dict(res["possible_split"]),
        "source_mix": dict(res["source_mix"]), "confusion": {f"gustavo_mca={a},ocrolus_mca={b}": n for (a, b), n in res["confusion"].items()},
    }
    json.dump(summary, open(os.path.join(args.out, "summary.json"), "w"), indent=1)
    write_csv(os.path.join(args.out, "crosstab.csv"), [{"ocrolus_tag": o, "gustavo_tag": g, "n": n} for (o, g), n in sorted(res["crosstab"].items(), key=lambda kv: -kv[1])])
    write_report(res, args.out, index, review_stats, usage)
    print(json.dumps(summary["totals"], indent=1))


if __name__ == "__main__":
    main()
