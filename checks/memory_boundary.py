"""Check that past.dev read boundaries behave the way the lab's memory design assumes.

It builds a tiny lab in the past.dev project (two departments, one worker each, one head),
writes one fact per department, and then reads as each agent. It reports:

  1. Does a worker see its own department's fact?
  2. Is a worker blocked from the other department's fact?
  3. Does a head see every department's fact?
  4. Is an identity past.dev has never seen blocked?
  5. Is a fact written with no audience readable by everyone? (issue I-2)
  6. After a head passes a fact down, can the other department's worker read the pass-down,
     and only the pass-down?
  7. How long does a write take to become readable? (issue I-3)

Everything it creates is named "check-..." and is deleted at the end unless --keep is given.

Run from the repository root:  python3 checks/memory_boundary.py
Needs PAST_API_KEY in the environment or in .env. Uses only the Python standard library.
"""

import argparse
import datetime as dt
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://api.past.dev/api/v1"


def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    if not os.environ.get("PAST_API_KEY"):
        sys.exit("PAST_API_KEY is not set (put it in .env)")


def call(method, path, body=None):
    """Return (status, parsed body, request id, seconds). Never raises on an HTTP error."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + os.environ["PAST_API_KEY"])
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "summit-lab-check/0.1")
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw, status, headers = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as err:
        raw, status, headers = err.read(), err.code, err.headers
    seconds = round(time.time() - start, 2)
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = {"raw": raw.decode(errors="replace")[:500]}
    return status, parsed, headers.get("X-Request-Id"), seconds


def must(result, what):
    status, parsed, request_id, _ = result
    if status >= 300:
        sys.exit(f"FAILED to {what}: HTTP {status} {json.dumps(parsed)[:400]} (request {request_id})")
    return parsed


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_fact(source_id, content, label, audience, author):
    """Send one fact. Returns (ingestion id, time sent)."""
    body = {"id": source_id, "content": content, "label": label, "timestamp": now(), "identity": author}
    if audience is not None:
        body["audience"] = audience
    parsed = must(call("POST", "/ingest", body), f"write {source_id}")
    return parsed["ingestionId"], time.time()


def wait_completed(ingestion_id, sent_at, limit=600):
    """Poll until past.dev reports the write as completed. Returns seconds since it was sent."""
    while time.time() - sent_at < limit:
        parsed = must(call("GET", f"/ingest/{ingestion_id}"), "poll a write")
        if parsed["status"] == "completed":
            return round(time.time() - sent_at, 1)
        if parsed["status"] == "failed":
            sys.exit(f"write {ingestion_id} failed: {parsed.get('failure')}")
        time.sleep(3)
    sys.exit(f"write {ingestion_id} did not complete in {limit} seconds")


def read(identity, query):
    """Read as one identity. Returns the text the reader got back, source ids, and call details."""
    status, parsed, request_id, seconds = call(
        "POST", "/recall", {"query": query, "identity": identity, "limit": 20}
    )
    if status >= 300:
        sys.exit(f"FAILED to read as {identity}: HTTP {status} {json.dumps(parsed)[:400]}")
    texts, source_ids, kinds = [], set(), []
    for result in parsed.get("results", []):
        texts.append(result.get("content") or "")
        kinds.append((result.get("artifact") or {}).get("kind"))
        for source in result.get("sources", []):
            source_ids.add(source.get("sourceId"))
            texts.extend(source.get("excerpts") or [])
    return {
        "identity": identity,
        "text": "\n".join(texts),
        "source_ids": sorted(s for s in source_ids if s),
        "kinds": kinds,
        "results": len(parsed.get("results", [])),
        "pending": parsed.get("pending"),
        "request_id": request_id,
        "seconds": seconds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--keep", action="store_true", help="do not delete the check data afterwards")
    args = parser.parse_args()
    load_env()

    run = dt.datetime.now().strftime("%m%d%H%M%S")
    code_a = f"{random.randint(10000, 99999)}"  # the value only department A should know
    code_b = f"{random.randint(10000, 99999)}"
    code_open = f"{random.randint(10000, 99999)}"
    worker_a, worker_b, head = "check-worker-a", "check-worker-b", "check-head"
    stranger = f"check-stranger-{run}"  # never registered with past.dev
    ids = {name: f"check-{run}-{name}" for name in ("fact-a", "fact-b", "open", "passdown")}
    checks, timings, reads = [], {}, []

    def record(name, expected, got, detail):
        checks.append({"check": name, "expected": expected, "got": got, "pass": expected == got, **detail})
        print(f"  {'PASS' if expected == got else 'FAIL'}  {name}: expected {expected}, got {got}")

    print(f"Run {run}. Setting up two departments, two workers and one head ...")
    must(call("PUT", f"/identities/{worker_a}", {"traits": {"check_department": "a", "check_role": "worker"}}), "create worker a")
    must(call("PUT", f"/identities/{worker_b}", {"traits": {"check_department": "b", "check_role": "worker"}}), "create worker b")
    must(call("PUT", f"/identities/{head}", {"traits": {"check_role": "head"}}), "create head")
    for dept in ("a", "b"):
        rule = {"match": "any", "conditions": [
            {"trait": "check_department", "operator": "is", "values": [dept]},
            {"trait": "check_role", "operator": "is", "values": ["head"]},
        ]}
        must(call("PUT", f"/audiences/check-dept-{dept}", {"kind": "rule", "name": f"Check department {dept.upper()}", "rule": rule}), f"create audience {dept}")
        must(call("POST", f"/audiences/check-dept-{dept}/recompute"), f"recompute audience {dept}")
    time.sleep(5)  # membership changes reach reads after a short window

    reached = {who: must(call("GET", f"/identities/{who}"), f"look up {who}") for who in (worker_a, worker_b, head)}
    memberships = {who: record_.get("audiences") for who, record_ in reached.items()}
    print("  audiences each identity reaches, as past.dev reports them:", json.dumps(memberships))

    print("Writing one fact per department and one fact with no audience ...")
    sent = {}
    sent["fact-a"] = write_fact(ids["fact-a"], f"Department A measured the calibration code for rig seven as {code_a} on its third trial.", "Department A lab note", "check-dept-a", worker_a)
    sent["fact-b"] = write_fact(ids["fact-b"], f"Department B found that reagent batch {code_b} is contaminated and must not be used.", "Department B lab note", "check-dept-b", worker_b)
    sent["open"] = write_fact(ids["open"], f"Notice with no audience set: the door code for the shared store room is {code_open}.", "Unscoped notice", None, head)

    early = read(worker_a, "What calibration code did department A measure for rig seven?")
    timings["own_fact_readable_right_after_write"] = code_a in early["text"]
    timings["seconds_after_write_at_first_read"] = round(time.time() - sent["fact-a"][1], 1)

    for name in ("fact-a", "fact-b", "open"):
        timings[f"seconds_to_completed_{name}"] = wait_completed(*sent[name])
    print("  seconds until past.dev reported each write completed:", {k: v for k, v in timings.items() if k.startswith("seconds_to_completed")})

    print("Reading as each agent ...")
    q_a = "What calibration code did department A measure for rig seven?"
    q_open = "What is the door code for the shared store room?"
    for who, expected in ((worker_a, True), (worker_b, False), (head, True), (stranger, False)):
        got = read(who, q_a)
        reads.append({"question": q_a, **{k: v for k, v in got.items() if k != "text"}})
        label = {worker_a: "1. worker reads its own department", worker_b: "2. worker is blocked from the other department",
                 head: "3. head reads every department", stranger: "4. unknown identity is blocked"}[who]
        record(label, expected, code_a in got["text"], {"reader": who, "request_id": got["request_id"]})
    got = read(head, "Which reagent batch did department B find contaminated?")
    reads.append({"question": "department B fact, read as head", **{k: v for k, v in got.items() if k != "text"}})
    record("3b. head reads the second department too", True, code_b in got["text"], {"reader": head, "request_id": got["request_id"]})
    for who in (worker_b, stranger):
        got = read(who, q_open)
        reads.append({"question": q_open, **{k: v for k, v in got.items() if k != "text"}})
        record(f"5. fact with no audience is readable by {'another department' if who == worker_b else 'an unknown identity'}",
               True, code_open in got["text"], {"reader": who, "request_id": got["request_id"]})

    print("Head passes department A's finding down to department B as a new fact ...")
    sent["passdown"] = write_fact(ids["passdown"], f"Passed down by the head researcher to department B: department A's calibration code for rig seven is {code_a}. Use it for rig seven.", "Pass-down from head", "check-dept-b", head)
    timings["seconds_to_completed_passdown"] = wait_completed(*sent["passdown"])
    got = read(worker_b, q_a)
    reads.append({"question": q_a + " (after pass-down)", **{k: v for k, v in got.items() if k != "text"}})
    record("6a. after the pass-down, the other department's worker can read the value", True, code_a in got["text"], {"reader": worker_b, "request_id": got["request_id"]})
    record("6b. ... and still cannot read department A's original note", False, ids["fact-a"] in got["source_ids"], {"reader": worker_b, "request_id": got["request_id"]})

    if not args.keep:
        print("Cleaning up ...")
        must(call("POST", "/data-points/delete", {"ids": list(ids.values())}), "delete the check facts")
        for dept in ("a", "b"):
            call("DELETE", f"/audiences/check-dept-{dept}")
        for who in (worker_a, worker_b, head):
            call("DELETE", f"/identities/{urllib.parse.quote(who)}")

    passed = sum(c["pass"] for c in checks)
    out = ROOT / "checks" / "results" / f"memory_boundary_{run}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "run": run, "finished": now(), "passed": passed, "total": len(checks),
        "memberships_reported_by_past_dev": memberships, "timings": timings,
        "checks": checks, "reads": reads, "cleaned_up": not args.keep,
    }, indent=2) + "\n")
    print(f"\n{passed} of {len(checks)} checks passed. Timings: {json.dumps(timings)}")
    print(f"Full record: {out.relative_to(ROOT)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
