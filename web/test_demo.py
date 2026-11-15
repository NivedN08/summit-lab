"""Tests for web/make_demo.py: choosing the 5 minutes the demo plays.

    python3 -m pytest web/test_demo.py -q
"""

import json
import pathlib
import sys

import pytest

WEB = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(WEB))
import make_demo as M  # noqa: E402


def fake_run(events, duration=1800.0, last_end=1500.0, finished=True):
    return {"run": "fake", "label": "fake", "started": "2026-11-14T23:00:00.000Z", "duration": duration,
            "finished": finished, "studies": [], "notes": {}, "searches": [],
            "agents": [{"id": "s1-team-a", "role": "team-a", "study": "s1", "model": "fable", "cost": 0,
                        "sessions": [{"phase": "analysis", "study": "s1", "start": 10.0, "end": last_end}]}],
            "events": events}


def test_the_busiest_window_is_where_things_move():
    quiet = [{"t": float(t), "k": "memory.write", "a": "s1-team-a"} for t in range(0, 1500, 60)]
    busy = [{"t": 900.0 + i * 10, "k": k} for i, k in enumerate(["compare", "referee.assign", "referee.verdict",
                                                                    "study.final", "candidate", "candidate"] * 3)]
    start, end, score, kinds = M.busiest(fake_run(sorted(quiet + busy, key=lambda e: e["t"])), 300)
    assert start <= 900 <= end and end - start <= 300
    assert kinds["compare"] == 3 and kinds["referee.verdict"] == 3


def test_variety_beats_more_of_the_same():
    # 20 notes outweigh these five events by raw weight (20 against 18); the variety bonus decides it.
    same = [{"t": 100.0 + i, "k": "memory.write"} for i in range(20)]
    varied = [{"t": 1000.0 + i * 20, "k": k} for i, k in enumerate(["compare", "candidate", "memory.pass_down",
                                                                     "referee.verdict", "study.final"])]
    start, end, _, _ = M.busiest(fake_run(same + varied), 300)
    assert start <= 1000 <= end


def test_last_ends_where_the_replay_ends_with_the_grading_inside():
    events = [{"t": 1700.0, "k": "grade.done"}, {"t": 1400.0, "k": "compare"}]
    run = fake_run(events, duration=1800, last_end=1500)
    start, end, _, kinds = M.last(run, 300)
    assert end == M.replay_end(run) == 1520 and start == 1220
    assert kinds["grade.done"] == 1 and kinds["compare"] == 1          # grading is pinned to the replay's end


def test_a_run_shorter_than_the_window_is_shown_whole():
    run = fake_run([{"t": 50.0, "k": "compare"}], duration=200, last_end=150)
    start, end, _, _ = M.busiest(run, 300)
    assert (start, end) == (0.0, 170.0)


def test_an_unfinished_run_plays_to_its_last_event():
    run = fake_run([], duration=1000, last_end=None, finished=False)
    assert M.replay_end(run) == 1000


def test_main_writes_demo_json_for_a_baked_run(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    run = fake_run([{"t": 1700.0, "k": "grade.done"}, {"t": 1400.0, "k": "compare"}])
    (runs / "fake.json").write_text(json.dumps(run))
    monkeypatch.setattr(M, "RUNS", runs)
    assert M.main(["--run", "fake", "--last", "--speed", "12"]) == 0
    demo = json.loads((runs / "demo.json").read_text())
    assert demo["run"] == "fake" and demo["mode"] == "last" and demo["speed"] == 12
    assert demo["end"] == 1520 and demo["start"] == 1220 and demo["ends_with_results"] is True


def test_an_unknown_run_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "RUNS", tmp_path)
    with pytest.raises(SystemExit):
        M.main(["--run", "no-such-run"])
