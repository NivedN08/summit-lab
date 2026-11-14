"""Tests for lab.eventlog.JsonlEventLog."""

import json
import pathlib
import re
import subprocess
import sys
import threading

import pytest

from lab.eventlog import JsonlEventLog

ROOT = pathlib.Path(__file__).resolve().parents[1]
TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


def test_append_adds_seq_time_kind_actor_and_data(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    first = log.append("memory.write", "worker-a", fact_id="f1", department="team-a")
    second = log.append("memory.recall", "worker-a", query="q", ids=["f1"])
    assert first["seq"] == 1 and second["seq"] == 2
    assert TIME.match(first["time"])
    assert first["kind"] == "memory.write" and first["actor"] == "worker-a"
    assert first["fact_id"] == "f1" and second["ids"] == ["f1"]
    lines = (tmp_path / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [1, 2]


def test_events_filters_by_kind_and_reads_fresh(tmp_path):
    path = tmp_path / "events.jsonl"
    reader = JsonlEventLog(path)
    assert reader.events() == []
    writer = JsonlEventLog(path)
    writer.append("a", "x")
    writer.append("b", "x")
    writer.append("a", "y")
    assert [e["seq"] for e in reader.events()] == [1, 2, 3]
    assert [e["actor"] for e in reader.events("a")] == ["x", "y"]


def test_seq_continues_after_reopening(tmp_path):
    path = tmp_path / "events.jsonl"
    JsonlEventLog(path).append("a", "x")
    assert JsonlEventLog(path).append("a", "x")["seq"] == 2


def test_reserved_keys_are_refused(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")
    with pytest.raises(ValueError):
        log.append("a", "x", seq=99)
    with pytest.raises(ValueError):
        log.append("a", "x", time="yesterday")


def test_concurrent_threads_get_unique_increasing_seq(tmp_path):
    log = JsonlEventLog(tmp_path / "events.jsonl")

    def work():
        for _ in range(50):
            log.append("t", threading.current_thread().name)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    seqs = [e["seq"] for e in log.events()]
    assert seqs == list(range(1, 401))


APPENDER = """
import sys
from lab.eventlog import JsonlEventLog
log = JsonlEventLog(sys.argv[1])
for i in range(int(sys.argv[2])):
    log.append("proc", sys.argv[3], i=i)
"""


def test_concurrent_processes_get_unique_increasing_seq(tmp_path):
    path = tmp_path / "events.jsonl"
    procs = [
        subprocess.Popen([sys.executable, "-c", APPENDER, str(path), "200", name], cwd=ROOT)
        for name in ("p1", "p2")
    ]
    local = JsonlEventLog(path)
    for i in range(100):
        local.append("proc", "main", i=i)
    for p in procs:
        assert p.wait(timeout=60) == 0
    events = local.events()
    assert [e["seq"] for e in events] == list(range(1, 501))
    by_actor = {name: [e["i"] for e in events if e["actor"] == name] for name in ("p1", "p2", "main")}
    assert by_actor["p1"] == list(range(200)) and by_actor["p2"] == list(range(200))
    assert by_actor["main"] == list(range(100))
