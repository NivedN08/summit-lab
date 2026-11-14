"""Exa research tool for the lab's agents. Every call is logged so each source can be traced.

Usage (from anywhere; the key is read from the repository's .env or the environment):

  python3 lab/tools/exa.py search "QUERY" --objective "what this search is for" --agent scout-1
  python3 lab/tools/exa.py search "QUERY" --objective "..." --type deep --num 10
  python3 lab/tools/exa.py contents URL [URL ...] --max-chars 6000 --agent scout-1

`search` returns titles, links, dates and the passages that match the query.
`contents` returns the text of pages you already have links for.

Every call appends one line to records/exa_calls.jsonl: who asked, why, what was asked,
what came back and what it cost. The tool refuses to run once the logged spend reaches
EXA_BUDGET_USD (default 25 dollars).

Uses only the Python standard library.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG = ROOT / "records" / "exa_calls.jsonl"
API = "https://api.exa.ai"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    if not os.environ.get("EXA_API_KEY"):
        sys.exit("EXA_API_KEY is not set (put it in .env)")


def spent():
    total = 0.0
    if LOG.exists():
        for line in LOG.read_text().splitlines():
            try:
                total += float(json.loads(line).get("cost_usd") or 0)
            except ValueError:
                pass
    return total


def post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("x-api-key", os.environ["EXA_API_KEY"])
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "summit-lab/0.1")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {"error": raw.decode(errors="replace")[:500]}
    return status, parsed, round(time.time() - start, 2)


def log_call(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Exa research tool with a call log")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="find pages")
    s.add_argument("query")
    s.add_argument("--objective", required=True, help="the goal this search serves; it steers ranking and goes in the log")
    s.add_argument("--type", choices=["auto", "lite", "deep-lite", "deep", "deep-reasoning"], help="leave out for the default")
    s.add_argument("--num", type=int, help="number of results; leave out for the default of 10")
    c = sub.add_parser("contents", help="read pages you already have links for")
    c.add_argument("urls", nargs="+")
    c.add_argument("--objective", default="", help="why these pages are being read; goes in the log")
    c.add_argument("--max-chars", type=int, default=6000, help="characters of text per page")
    for p in (s, c):
        p.add_argument("--agent", default=os.environ.get("LAB_AGENT", "unknown"), help="who is asking")
        p.add_argument("--json", action="store_true", help="print the raw response")
    args = parser.parse_args()
    load_env()

    cap = float(os.environ.get("EXA_BUDGET_USD", "25"))
    used = spent()
    if used >= cap:
        sys.exit(f"Exa budget reached: ${used:.2f} logged of ${cap:.2f}. Raise EXA_BUDGET_USD to continue.")

    if args.command == "search":
        body = {"query": args.query, "objective": args.objective, "contents": {"highlights": True}}
        if args.type:
            body["type"] = args.type
        if args.num:
            body["numResults"] = args.num
        status, parsed, seconds = post("/search", body)
        asked = {"query": args.query, "type": args.type or "auto"}
    else:
        body = {"urls": args.urls, "text": {"maxCharacters": args.max_chars}}
        status, parsed, seconds = post("/contents", body)
        asked = {"urls": args.urls}

    results = parsed.get("results") or []
    cost = (parsed.get("costDollars") or {}).get("total")
    log_call({
        "time": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": args.agent, "endpoint": args.command, "objective": args.objective, **asked,
        "status": status, "seconds": seconds, "cost_usd": cost,
        "results": [{"title": r.get("title"), "url": r.get("url"), "published": (r.get("publishedDate") or "")[:10]} for r in results],
    })
    if status >= 300:
        sys.exit(f"Exa returned HTTP {status}: {json.dumps(parsed)[:400]}")
    if args.json:
        print(json.dumps(parsed, indent=2))
        return
    print(f"[{len(results)} results, {seconds}s, ${cost}; ${used + (cost or 0):.3f} of ${cap:.0f} budget used]")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r.get('title')}\n   {r.get('url')}   {(r.get('publishedDate') or '')[:10]}")
        if r.get("highlights"):
            for h in r["highlights"][:3]:
                print("   > " + " ".join(h.split())[:600])
        if r.get("text"):
            print(r["text"])


if __name__ == "__main__":
    main()
