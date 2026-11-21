"""The past.dev namespace a lab run gets (lab.run.make_memory): unique per run folder, stable across resumes.

Two labs once ran at the same time in runs/heldout/biodsa/lab and runs/heldout/planted/lab. The namespace
was built from the folder's last name only, so both were "sl-lab", they shared the director identity, and
the second lab's membership check refused to start (I-17). No past.dev calls here."""

import json

from lab import run
from lab.eventlog import JsonlEventLog
from lab.memory_past import NAMESPACE


def namespace_for(run_dir):
    run_dir.mkdir(parents=True, exist_ok=True)
    return run.make_memory("past", JsonlEventLog(run_dir / "events.jsonl"), run_dir).namespace


def test_two_run_folders_with_the_same_name_get_different_namespaces(tmp_path):
    a = namespace_for(tmp_path / "heldout" / "biodsa" / "lab")
    b = namespace_for(tmp_path / "heldout" / "planted" / "lab")
    assert a != b
    for ns in (a, b):
        assert NAMESPACE.match(ns) and len(ns) <= 31 and ns.startswith("sl-lab-")


def test_the_same_folder_gets_the_same_namespace_every_time(tmp_path):
    assert namespace_for(tmp_path / "r" / "lab") == namespace_for(tmp_path / "r" / "lab")


def test_a_long_folder_name_still_fits(tmp_path):
    ns = namespace_for(tmp_path / ("a-very-long-run-folder-name-" * 3))
    assert NAMESPACE.match(ns) and len(ns) <= 31


def test_a_resumed_run_keeps_the_namespace_its_memory_was_set_up_under(tmp_path):
    """A run set up before this change (namespace "sl-lab") must resume into the same memory."""
    run_dir = tmp_path / "heldout" / "biodsa" / "lab"
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(json.dumps(
        {"seq": 1, "time": "2026-11-19T20:28:00.000Z", "kind": "memory.setup", "actor": "memory",
         "backend": "past", "namespace": "sl-lab"}) + "\n")
    assert namespace_for(run_dir) == "sl-lab"
