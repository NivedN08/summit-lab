"""Tests for lab.prereg: freezing a spec, checking runs against it, and --prereg on the command line.

Each test works on a temporary copy of lab/ (prompts and code), so editing a file never touches the repo.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from lab import ledger, prereg, protocol, run
from test_protocol import FakeAdapter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = {"batch": "b1", "benchmark": "fake", "studies": ["s1", "s2"], "arms": ["lab", "fable alone"],
        "config": {"worker_model": "fable", "challenge": True, "brief_teams": ["team-a"]},
        "seeds": [1], "max_usd": 40, "primary_endpoint": "lab passes of 2", "stop_rules": ["stop at the cap"],
        "notes": "test batch"}


@pytest.fixture
def root(tmp_path):
    """A copy of the repository's lab/ folder (no caches), standing in for the repository root."""
    copy = tmp_path / "repo"
    shutil.copytree(ROOT / "lab", copy / "lab", ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "prereg").mkdir()
    return copy


def write_spec(root, **changes) -> pathlib.Path:
    spec = {**SPEC, **changes}
    path = root / "prereg" / f"{spec['batch']}.json"
    path.write_text(json.dumps(spec, indent=2))
    return path


def test_the_hash_is_stable(root):
    spec = write_spec(root)
    sha = prereg.freeze(spec, root=root)
    assert len(sha) == 64
    assert prereg.freeze(spec, root=root) == sha              # freezing again with nothing changed
    assert prereg.check(spec, root=root) == sha
    lock = json.loads((root / "prereg" / "b1.lock").read_text())
    assert lock["sha256"] == sha and lock["git_head"] is None    # the copy is not a git checkout
    assert "lab/prompts/analyst.md" in lock["files"] and "lab/protocol.py" in lock["files"]
    # the key order in the spec file does not matter: the hash covers canonical JSON
    spec.write_text(json.dumps(dict(reversed(list(SPEC.items())))))
    assert prereg.check(spec, root=root) == sha


def test_editing_a_prompt_makes_check_refuse(root):
    spec = write_spec(root)
    prereg.freeze(spec, root=root)
    prompt = root / "lab" / "prompts" / "analyst.md"
    prompt.write_text(prompt.read_text() + "\nOne more line.\n")
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, root=root)
    assert any(p.startswith("lab/prompts/analyst.md: changed since freezing") for p in err.value.problems)
    assert "lab/prompts/analyst.md" in str(err.value)


def test_adding_or_removing_lab_code_makes_check_refuse(root):
    spec = write_spec(root)
    prereg.freeze(spec, root=root)
    (root / "lab" / "extra.py").write_text("x = 1\n")
    (root / "lab" / "report.py").unlink()
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, root=root)
    assert set(err.value.problems) == {"lab/extra.py: added since freezing", "lab/report.py: removed since freezing"}


def test_study_benchmark_or_config_mismatch_refuses(root):
    spec = write_spec(root)
    sha = prereg.freeze(spec, root=root)
    good = protocol.Config(worker_model="fable")
    assert prereg.check(spec, benchmark="fake", studies=["s2", "s1"], config=good, root=root) == sha   # order is free
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, benchmark="fake", studies=["s1"], config=good, root=root)
    assert err.value.problems == ["studies differ from the registration: not in this run: s2"]
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, benchmark="fake", studies=["s1", "s2", "s3"], config=good, root=root)
    assert "not registered: s3" in err.value.problems[0]
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, benchmark="genebench", studies=["s1", "s2"], config=good, root=root)
    assert err.value.problems == ["benchmark: registered 'fake', this run 'genebench'"]
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, benchmark="fake", studies=["s1", "s2"], root=root,
                     config=protocol.Config(worker_model="opus", challenge=False))
    assert err.value.problems == ["config challenge: registered True, this run False",
                                  "config worker_model: registered 'fable', this run 'opus'"]


def test_a_changed_spec_refuses_and_verify_lock_sees_it(root):
    spec = write_spec(root)
    prereg.freeze(spec, root=root)
    assert prereg.verify_lock(spec) == []
    (root / "lab" / "report.py").write_text("# the code moved on after the batch\n")
    assert prereg.verify_lock(spec) == []              # a lock records its spec; the code may move on later
    write_spec(root, notes="edited after freezing")
    assert prereg.verify_lock(spec) == ["the spec changed since it was frozen"]
    with pytest.raises(prereg.PreregError):
        prereg.check(spec, root=root)
    with pytest.raises(prereg.PreregError) as err:
        prereg.freeze(spec, root=root)                 # a registration is never overwritten
    assert "never overwritten" in str(err.value)


def test_an_edited_lock_is_caught(root):
    spec = write_spec(root)
    prereg.freeze(spec, root=root)
    lock_file = root / "prereg" / "b1.lock"
    lock = json.loads(lock_file.read_text())
    lock["files"]["lab/prompts/analyst.md"] = "0" * 64
    lock_file.write_text(json.dumps(lock))
    assert prereg.verify_lock(spec) == ["the lock's sha256 does not match its own parts: the lock was edited"]


def test_bad_specs_are_refused(root):
    with pytest.raises(prereg.PreregError) as err:
        prereg.freeze(write_spec(root, config={"no_such_field": 1}), root=root)
    assert '"config" names "no_such_field", which is not a Config field' in err.value.problems
    path = root / "prereg" / "other.json"
    path.write_text(json.dumps({k: v for k, v in SPEC.items() if k != "stop_rules"}))
    with pytest.raises(prereg.PreregError) as err:
        prereg.freeze(path, root=root)
    assert 'missing field "stop_rules"' in err.value.problems
    assert any("must be called b1.json" in p for p in err.value.problems)
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(write_spec(root, batch="b2"), root=root)
    assert "freeze the spec first" in err.value.problems[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_head_may_move_only_by_commits_outside_lab(root):
    def git(*args):
        subprocess.run(["git", "-C", str(root), "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                       check=True, capture_output=True)

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "base")
    spec = write_spec(root)
    sha = prereg.freeze(spec, root=root)
    assert json.loads((root / "prereg" / "b1.lock").read_text())["git_head"]
    git("add", "-A")
    git("commit", "-qm", "register the batch")             # committing the lock moves HEAD
    assert prereg.check(spec, root=root) == sha
    (root / "lab" / "tools" / "notes.txt").write_text("not a .py or .md file, so no file hash covers it\n")
    git("add", "-A")
    git("commit", "-qm", "change lab")
    with pytest.raises(prereg.PreregError) as err:
        prereg.check(spec, root=root)
    assert any(p.startswith("lab/tools/notes.txt: changed in a commit since freezing") for p in err.value.problems)


# ----- the command line -----

def test_a_matching_check_passes_and_is_logged(root, tmp_path, monkeypatch):
    adapter = FakeAdapter(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    monkeypatch.setattr(prereg, "ROOT", root)
    spec = write_spec(root)
    assert run.main(["prereg", "freeze", str(spec)]) == 0
    sha = json.loads((root / "prereg" / "b1.lock").read_text())["sha256"]
    assert run.main(["prereg", "check", str(spec)]) == 0
    run_dir = tmp_path / "run"
    common = ["--benchmark", "fake", "--studies", "s1,s2", "--backend", "mock", "--run-dir", str(run_dir),
              "--prereg", str(spec)]
    assert run.main(["lab", *common]) == 0
    assert run.main(["baseline", *common, "--attempts", "1"]) == 0
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text().splitlines()]
    checked = [e for e in events if e["kind"] == "prereg.checked"]
    assert [(e["command"], e["sha256"]) for e in checked] == [("lab", sha), ("baseline", sha)]
    assert events.index(checked[0]) < min(i for i, e in enumerate(events) if e["kind"] == "agent.start")
    assert json.loads((run_dir / "run.json").read_text())["prereg"] == {"spec": str(spec), "sha256": sha}


def test_a_mismatching_run_refuses_before_anything_starts(root, tmp_path, monkeypatch):
    adapter = FakeAdapter(tmp_path / "bench")
    monkeypatch.setitem(run.ADAPTERS, "fake", lambda: adapter)
    monkeypatch.setattr(prereg, "ROOT", root)
    spec = write_spec(root)
    prereg.freeze(spec, root=root)
    run_dir = tmp_path / "run"
    base = ["lab", "--benchmark", "fake", "--backend", "mock", "--run-dir", str(run_dir), "--prereg", str(spec)]
    with pytest.raises(SystemExit) as err:
        run.main(base + ["--studies", "s1"])
    assert "not in this run: s2" in str(err.value)
    with pytest.raises(SystemExit) as err:
        run.main(base + ["--studies", "s1,s2", "--model", "opus"])
    assert "config worker_model: registered 'fable', this run 'opus'" in str(err.value)
    assert not run_dir.exists()


def test_prereg_freeze_can_register_the_batch_in_the_ledger(root, tmp_path, monkeypatch):
    monkeypatch.setattr(prereg, "ROOT", root)
    spec = write_spec(root)
    book = tmp_path / "ledger.jsonl"
    assert run.main(["prereg", "freeze", str(spec), "--ledger", str(book)]) == 0
    [entry] = ledger.entries(book)
    assert entry["kind"] == "batch.registered" and entry["data"]["batch"] == "b1"
    assert entry["data"]["sha256"] == json.loads((root / "prereg" / "b1.lock").read_text())["sha256"]
    assert ledger.verify_chain(book) == []
