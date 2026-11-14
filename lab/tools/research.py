"""Web research for lab agents, through the lab gateway. Holds no API key.

  python3 lab/tools/research.py search "QUERY" --objective "what this search is for" [--type deep] [--num 10]
  python3 lab/tools/research.py contents URL [URL ...] --objective "why you are reading these" [--max-chars 6000]

`search` returns titles, links, dates and the passages that match. `contents` returns the
text of pages you already have links for. Only some departments may use this tool; the
gateway refuses the others, and it hides some sources from every agent.

Needs LAB_GATEWAY_URL and LAB_GATEWAY_TOKEN in the environment. Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(path, body):
    """POST to the gateway and return the parsed answer, or exit with the error."""
    url, token = os.environ.get("LAB_GATEWAY_URL"), os.environ.get("LAB_GATEWAY_TOKEN")
    if not url or not token:
        sys.exit("error: LAB_GATEWAY_URL and LAB_GATEWAY_TOKEN must be set")
    req = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # the gateway is local
    try:
        with opener.open(req, timeout=300) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            message = json.loads(err.read()).get("error")
        except ValueError:
            message = ""
        sys.exit(f"error: HTTP {err.code}: {message}")
    except urllib.error.URLError as err:
        sys.exit(f"error: cannot reach the gateway: {err.reason}")


def main():
    parser = argparse.ArgumentParser(description="Web research through the lab gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("search", help="find pages")
    s.add_argument("query")
    s.add_argument("--objective", required=True, help="the goal this search serves; it steers ranking")
    s.add_argument("--type", choices=["auto", "lite", "deep-lite", "deep", "deep-reasoning"])
    s.add_argument("--num", type=int, help="number of results (default 10)")
    c = sub.add_parser("contents", help="read pages you already have links for")
    c.add_argument("urls", nargs="+")
    c.add_argument("--objective", required=True, help="why you are reading these pages")
    c.add_argument("--max-chars", type=int, default=6000, help="characters of text per page")
    args = parser.parse_args()

    if args.command == "search":
        body = {"query": args.query, "objective": args.objective}
        if args.type:
            body["type"] = args.type
        if args.num:
            body["num"] = args.num
        out = call("/exa/search", body)
    else:
        out = call("/exa/contents", {"urls": args.urls, "objective": args.objective, "max_chars": args.max_chars})

    results = out.get("results") or []
    print(f"[{len(results)} results, {out.get('seconds')}s]")
    for i, r in enumerate(results, 1):
        print(f"\n{i}. {r.get('title')}\n   {r.get('url')}   {r.get('published') or ''}")
        for h in (r.get("highlights") or [])[:3]:
            print("   > " + " ".join(h.split())[:600])
        if r.get("text"):
            print(r["text"])


if __name__ == "__main__":
    main()
