"""Shared memory for lab agents. Talks only to the lab gateway; holds no API key.

  python3 lab/tools/memory.py write "what you found" --label "short title"
  python3 lab/tools/memory.py recall "your question" [--limit 20]
  python3 lab/tools/memory.py pass-down --to DEPARTMENT --from FACT_ID,FACT_ID --reason "why they need it" "text"

`write` saves a fact to your own department. `recall` returns what you are allowed to read
that matches the question. `pass-down` (heads and the director only) writes a new fact into
another department, naming the facts it is based on.

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
        with opener.open(req, timeout=120) as resp:
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
    parser = argparse.ArgumentParser(description="Shared memory for lab agents")
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("write", help="save a fact to your own department")
    w.add_argument("text")
    w.add_argument("--label", default="note", help="short title for the fact")
    r = sub.add_parser("recall", help="read what you may see that matches a question")
    r.add_argument("question")
    r.add_argument("--limit", type=int, default=20)
    p = sub.add_parser("pass-down", help="heads only: write a new fact into another department")
    p.add_argument("text")
    p.add_argument("--to", required=True, help="the department that receives it")
    p.add_argument("--from", dest="came_from", default="", help="comma-separated ids of the facts it is based on")
    p.add_argument("--reason", required=True, help="why that department needs it")
    args = parser.parse_args()

    if args.command == "write":
        out = call("/memory/write", {"text": args.text, "label": args.label})
        print(f"wrote {out['id']} to department {out['department']}")
    elif args.command == "pass-down":
        ids = [x.strip() for x in args.came_from.split(",") if x.strip()]
        out = call("/memory/pass_down", {"to_department": args.to, "text": args.text, "came_from": ids, "reason": args.reason})
        print(f"passed down {out['id']} to department {out['to_department']}")
    else:
        facts = call("/memory/recall", {"query": args.question, "limit": args.limit})["facts"]
        if not facts:
            print("No facts found.")
            return
        print(f"{len(facts)} facts. They are what you may read that came closest; check that each one answers your question.")
        for i, f in enumerate(facts, 1):
            who = ", ".join(x for x in (f.get("kind"), f.get("department") and "department " + f["department"],
                                        f.get("author") and "by " + f["author"], (f.get("occurred_at") or "")[:16]) if x)
            print(f"\n[{i}] {f['id']}  ({who})")
            print("    " + " ".join((f.get("text") or "").split()))
            if f.get("came_from"):
                print("    came from: " + ", ".join(f["came_from"]))


if __name__ == "__main__":
    main()
