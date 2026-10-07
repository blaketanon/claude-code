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
import urllib.request


def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def list_runs(base):
    """GET /api/runs. Accepts either a bare array or {runs: [...]} with optional
    cursor/next paging; follows paging when present."""
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
    args = ap.parse_args()

    base = args.base.rstrip("/")
    os.makedirs(os.path.join(args.out, "runs"), exist_ok=True)

    print(f"GET {base}/api/health", file=sys.stderr)
    try:
        print(json.dumps(get_json(f"{base}/api/health")), file=sys.stderr)
    except urllib.error.URLError as e:
        sys.exit(f"cannot reach {base}: {e}")

    runs = list_runs(base)
    with open(os.path.join(args.out, "runs_index.json"), "w") as f:
        json.dump(runs, f, indent=1)
    print(f"{len(runs)} runs listed", file=sys.stderr)

    for name in ("review/stats", "models"):
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
        fetched += 1
        time.sleep(args.sleep)
    print(f"saved {fetched} run files, skipped {skipped} unfinished, {failed} failed", file=sys.stderr)


if __name__ == "__main__":
    main()
