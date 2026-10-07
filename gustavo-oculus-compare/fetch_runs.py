#!/usr/bin/env python3
"""Pull every GustavoAI run (and the review statistics) from the GustavoAI API
and save them as JSON files so compare.py can work offline.

Usage:
    python3 fetch_runs.py --base https://gus.geroai.fund --out data

The API has no authentication today. Only finished runs (status "done") are
fetched in full; the run list is saved as-is so failed/cancelled runs are
still counted.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def list_runs(base):
    """GET /api/runs returns {runs: [...]} with the newest 100 runs and no
    paging. Older runs are still counted by /api/review/stats; to pull their
    detail, pass their ids with --ids. Bare arrays and cursor paging are
    tolerated in case the API grows them."""
    runs = []
    url = f"{base}/api/runs"
    seen_urls = set()
    while url and url not in seen_urls:
        seen_urls.add(url)
        data = get_json(url)
        if isinstance(data, list):
            runs.extend(data)
            break
        page = data.get("runs") or data.get("items") or data.get("data") or []
        runs.extend(page)
        nxt = data.get("next") or data.get("nextCursor") or data.get("cursor")
        if nxt and not str(nxt).startswith("http"):
            sep = "&" if "?" in f"{base}/api/runs" else "?"
            url = f"{base}/api/runs{sep}cursor={nxt}"
        else:
            url = nxt
    return runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("GUSTAVO_BASE", "https://gus.geroai.fund"))
    ap.add_argument("--out", default="data")
    ap.add_argument("--all", action="store_true", help="fetch detail for every run, not only status=done")
    ap.add_argument("--sleep", type=float, default=0.05, help="pause between detail requests")
    ap.add_argument("--ids", default="", help="file with extra run ids (one per line) not in the newest-100 list")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    os.makedirs(os.path.join(args.out, "runs"), exist_ok=True)
    os.makedirs(os.path.join(args.out, "review"), exist_ok=True)

    print(f"GET {base}/api/health", file=sys.stderr)
    try:
        print(json.dumps(get_json(f"{base}/api/health")), file=sys.stderr)
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {base}: {e}")

    runs = list_runs(base)
    known = {r.get("id") for r in runs}
    if args.ids:
        for line in open(args.ids):
            rid = line.strip()
            if rid and rid not in known:
                runs.append({"id": rid, "status": "done"})
                known.add(rid)
    with open(os.path.join(args.out, "runs_index.json"), "w") as f:
        json.dump(runs, f, indent=1)
    print(f"{len(runs)} runs listed", file=sys.stderr)

    # Cross-run aggregates kept by the app itself. /api/review/stats and
    # /api/usage cover every run in Postgres, not only the 100 the list shows.
    for name in ("review/stats", "usage", "models", "admin/migrations"):
        try:
            d = get_json(f"{base}/api/{name}")
            with open(os.path.join(args.out, name.replace("/", "_") + ".json"), "w") as f:
                json.dump(d, f, indent=1)
        except Exception as e:  # noqa: BLE001
            print(f"skip /api/{name}: {e}", file=sys.stderr)

    fetched = skipped = failed = 0
    for r in runs:
        rid = r.get("id")
        status = r.get("status")
        if not rid:
            continue
        if status != "done" and not args.all:
            skipped += 1
            continue
        path = os.path.join(args.out, "runs", f"{rid}.json")
        if os.path.exists(path):
            fetched += 1
            continue
        try:
            d = get_json(f"{base}/api/runs/{rid}", timeout=120)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAILED {rid}: {e}", file=sys.stderr)
            continue
        with open(path, "w") as f:
            json.dump(d, f)
        # the review view for the same run: one row per label with Ocrolus's
        # tag, the verdict and the latest reviewer feedback, for cross-checking
        try:
            rv = get_json(f"{base}/api/review?runId={urllib.parse.quote(rid)}&limit=5000", timeout=120)
            with open(os.path.join(args.out, "review", f"{rid}.json"), "w") as f:
                json.dump(rv, f)
        except Exception as e:  # noqa: BLE001
            print(f"review rows unavailable for {rid}: {e}", file=sys.stderr)
        fetched += 1
        time.sleep(args.sleep)
    print(f"saved {fetched} run files, skipped {skipped} unfinished, {failed} failed", file=sys.stderr)


if __name__ == "__main__":
    main()
