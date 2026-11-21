"""Tests for verify_results.py and claims.json: every published number recomputes from results/."""

import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import verify_results  # noqa: E402

CLAIMS = json.loads((ROOT / "claims.json").read_text())["claims"]


@pytest.fixture(autouse=True)
def no_repo_ledger(tmp_path, monkeypatch):
    """Point the ledger check at an empty path, so a ledger written elsewhere cannot change these tests."""
    monkeypatch.setenv("LAB_LEDGER", str(tmp_path / "no-ledger.jsonl"))


def test_the_real_claims_pass(capsys):
    assert verify_results.main([]) == 0
    out = capsys.readouterr().out
    assert " 0 FAIL" in out
    assert out.count(" OK ") >= len([c for c in CLAIMS if "unverifiable" not in c])


def test_claims_are_well_formed():
    ids = [c["id"] for c in CLAIMS]
    assert len(ids) == len(set(ids))
    for c in CLAIMS:
        assert {"id", "text", "value"} <= set(c)
        assert ("unverifiable" in c) != ("expr" in c), c["id"]
        if "expr" in c:
            assert all(str(p).startswith("results/") and (ROOT / p / "results.json").is_file()
                       for p in c["sources"].values()), c["id"]
    for needed in ("gb.lab", "sc.lab", "bd.lab", "gb.cost.lab", "sc.cost.lab", "bd.cost.lab", "sc.opus.first"):
        assert needed in ids


def tampered(tmp_path, claim_id, value) -> pathlib.Path:
    doc = json.loads((ROOT / "claims.json").read_text())
    for c in doc["claims"]:
        if c["id"] == claim_id:
            c["value"] = value
    path = tmp_path / "claims.json"
    path.write_text(json.dumps(doc))
    return path


@pytest.mark.parametrize("claim_id,value", [("gb.lab", [4, 8]), ("sc.cost.lab", 99.99), ("sc.selection.missed", ["sc11"])])
def test_a_tampered_claim_fails_with_exit_1(tmp_path, capsys, claim_id, value):
    assert verify_results.main(["--claims", str(tampered(tmp_path, claim_id, value))]) == 1
    out = capsys.readouterr().out
    assert f"FAIL {claim_id}:" in out and " 1 FAIL" in out


def test_the_script_exits_1_from_the_command_line(tmp_path):
    out = subprocess.run([sys.executable, str(ROOT / "verify_results.py"), "--claims",
                          str(tampered(tmp_path, "bd.lab", [28, 36]))], capture_output=True, text=True, cwd=tmp_path)
    assert out.returncode == 1 and "FAIL bd.lab" in out.stdout


def test_an_expression_may_not_reach_dunder_attributes(tmp_path):
    doc = {"claims": [{"id": "x", "text": "x", "value": 1, "sources": {"run": "results/test-v2"},
                       "expr": "run.__class__"}]}
    path = tmp_path / "claims.json"
    path.write_text(json.dumps(doc))
    assert verify_results.main(["--claims", str(path)]) == 1


def test_a_broken_ledger_fails_and_an_intact_one_passes(tmp_path, monkeypatch, capsys):
    from lab import ledger
    book = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LAB_LEDGER", str(book))
    ledger.append("note", {"text": "a"})
    ledger.append("note", {"text": "b"})
    small = tmp_path / "claims.json"
    small.write_text(json.dumps({"claims": [c for c in CLAIMS if c["id"] == "gb.lab"]}))
    assert verify_results.main(["--claims", str(small)]) == 0
    assert "2 entries, chain intact" in capsys.readouterr().out
    book.write_text(book.read_text().replace('"a"', '"z"'))
    assert verify_results.main(["--claims", str(small)]) == 1


def test_prereg_locks_are_checked_against_their_specs(tmp_path, monkeypatch, capsys):
    from lab import prereg
    root = tmp_path / "repo"
    (root / "prereg").mkdir(parents=True)
    (root / "lab" / "prompts").mkdir(parents=True)
    (root / "lab" / "prompts" / "x.md").write_text("prompt\n")
    spec = {"batch": "b1", "benchmark": "fake", "studies": ["s1"], "arms": [], "config": {}, "seeds": [],
            "max_usd": 1, "primary_endpoint": "x", "stop_rules": [], "notes": ""}
    (root / "prereg" / "b1.json").write_text(json.dumps(spec))
    prereg.freeze(root / "prereg" / "b1.json", root=root)
    monkeypatch.setattr(verify_results, "ROOT", root)
    small = tmp_path / "claims.json"
    small.write_text(json.dumps({"claims": []}))
    assert verify_results.main(["--claims", str(small)]) == 0
    assert "prereg/b1.lock" in capsys.readouterr().out
    (root / "prereg" / "b1.json").write_text(json.dumps({**spec, "notes": "changed"}))
    assert verify_results.main(["--claims", str(small)]) == 1
