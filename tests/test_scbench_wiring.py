"""Offline tests for wiring scBench into the lab: the answer file name taken from the study, a
library department per study, per-study sandbox specs on Modal, the early checks in lab.run,
and the sealed grading environment.

No Modal calls and no agents: scripted fake backends, LocalMemory and the local Gateway. Tests
that need the staged benchmark or the grading environment are skipped without them. No test
prints an answer value or a tolerance.
"""

import json
import pathlib
import sys
import types

import pytest

from lab import backend_modal as bm
from lab import protocol, run
from lab.backend_mock import MockBackend
from lab.baseline import run_baselines
from lab.contracts import DIRECTOR, WORKER, Agent, AgentResult, Study
from lab.eventlog import JsonlEventLog
from lab.memory_local import LocalMemory

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"


def no_exa(path, body):
    raise AssertionError("no Exa call expected in these tests")


# ----- a GeneBench-shaped and an scBench-shaped fake adapter -----

class FakeAdapter:
    """Two studies whose answer has one number and one string, in `deliverable`.
    `wrapped`: the answer is {"answer": {...}} (GeneBench) or the bare object (scBench)."""

    name = "fake"

    def __init__(self, root: pathlib.Path, deliverable: str, wrapped: bool, statement: str = "Estimate the effect."):
        self.root, self.deliverable, self.wrapped, self.statement = root, deliverable, wrapped, statement
        self.graded = []
        for sid in ("s1", "s2"):
            (root / sid / "data").mkdir(parents=True, exist_ok=True)

    def studies(self):
        return [Study(sid, sid, self.statement, self.root / sid / "data", [self.deliverable]) for sid in ("s1", "s2")]

    def schema(self, study):
        return {"effect": "number", "label": "string"}

    def _load(self, path):
        try:
            doc = json.loads(pathlib.Path(path).read_text())
        except (OSError, ValueError):
            return None
        if self.wrapped:
            doc = doc.get("answer") if isinstance(doc, dict) else None
        return doc if isinstance(doc, dict) else None

    def problems(self, study, deliverable, candidate):
        ans = self._load(candidate)
        if ans is None or set(ans) != {"effect", "label"}:
            return ["not an answer object with effect and label"]
        return [] if isinstance(ans["effect"], (int, float)) else ["effect must be a number"]

    def field_agreement(self, study, a, b):
        x, y = self._load(a) or {}, self._load(b) or {}
        return {"effect": x.get("effect") == y.get("effect"), "label": x.get("label") == y.get("label")}

    def agreement(self, study, deliverable, a, b):
        f = self.field_agreement(study, a, b)
        return sum(f.values()) / len(f)

    def grade(self, study, final_dir):
        path = pathlib.Path(final_dir) / self.deliverable
        self.graded.append(path.name)
        ans = self._load(path) or {}
        return {"passed": ans.get("effect") == 1.0, "score": 1.0 if ans.get("effect") == 1.0 else 0.0}


def make_lab(tmp_path, adapter, backend, config=None, **kwargs):
    run_dir = tmp_path / "run"
    log = JsonlEventLog(run_dir / "events.jsonl")
    memory = LocalMemory(log, run_dir / "memory.jsonl")
    lab = protocol.Lab(adapter, backend, memory, log, run_dir, config or protocol.Config(max_agents=3),
                       exa_call=no_exa, **kwargs)
    return lab, log, memory, run_dir


# ----- 2. GeneBench briefs are word for word as before -----

def test_genebench_analyst_and_baseline_briefs_are_unchanged(tmp_path):
    """The golden files are the briefs the lab produced before the answer file became a field."""
    statement = 'Estimate the effect.\nReturn {"answer": {"effect": <float>}}.\n'
    root = tmp_path / "bench"
    (root / "gb01" / "data").mkdir(parents=True)
    study = Study("gb01", "gb01", statement, root / "gb01" / "data", ["answer.json"])

    class Adapter:
        name = "fake-genebench"

        def studies(self):
            return [study]

        def problems(self, study, deliverable, candidate):
            return []

        def field_agreement(self, study, a, b):
            return {"effect": True}

        def agreement(self, study, d, a, b):
            return 1.0

    def script(agent, brief, ws):
        (ws / "answer.json").write_text(json.dumps({"answer": {"effect": 1.0}}))
        return {"answer_file": "answer.json", "method": "m", "questions": [], "verdict": "v"}

    backend = MockBackend(script)
    lab, log, _, run_dir = make_lab(tmp_path, Adapter(), backend, protocol.Config(max_agents=2, library=False))
    lab.run([study])
    run_baselines(Adapter(), backend, log, run_dir, [study], 1, "opus", 1)

    def brief(agent_id):
        text = next(c.brief for c in backend.calls if c.agent_id == agent_id)
        return text.replace(str(protocol.TOOLS), "<TOOLS>").replace(str(study.data_dir), "<DATA>")

    assert brief("gb01-team-a") == (GOLDEN / "genebench_analyst_brief.md").read_text()
    assert brief("baseline-gb01-1") == (GOLDEN / "genebench_baseline_brief.md").read_text()


# ----- 2. the answer file comes from the study -----

def test_every_brief_and_file_uses_the_studys_answer_file(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "eval_answer.json", wrapped=False)
    backend = MockBackend(run.mock_script(adapter))      # team B disagrees, then converges
    lab, log, _, run_dir = make_lab(tmp_path, adapter, backend)
    out = lab.run(adapter.studies())
    assert {sid: info["status"] for sid, info in out.items()} == {"s1": "converged", "s2": "converged"}
    analyst_briefs = [c.brief for c in backend.calls if "# Your job: analyst" in c.brief]
    assert len(analyst_briefs) == 8          # two teams, two rounds, two studies
    assert all("`eval_answer.json`" in b and "`answer.json`" not in b for b in analyst_briefs)
    for sid in ("s1", "s2"):
        final = run_dir / sid / "final"
        assert (final / "eval_answer.json").is_file() and not (final / "answer.json").exists()
        record = json.loads((run_dir / sid / "study.json").read_text())
        assert record["final"]["path"].endswith("final/eval_answer.json")
        assert all(c["valid"] for c in record["candidates"])

    run_baselines(adapter, backend, log, run_dir, adapter.studies(), 2, "opus", 2)
    baseline_briefs = [c.brief for c in backend.calls if c.agent_id.startswith("baseline-")]
    assert baseline_briefs and all("`eval_answer.json`" in b and '{"answer_file": "eval_answer.json"}' in b
                                   and "`answer.json`" not in b for b in baseline_briefs)
    assert (run_dir / "baseline" / "s1" / "attempt-2" / "eval_answer.json").is_file()

    results = run.grade_run(run_dir, adapter)
    assert set(adapter.graded) == {"eval_answer.json"}
    entry = results["studies"]["s1"]
    assert entry["lab"]["grade"]["passed"] is True
    assert all(r.get("error") is None for r in entry["candidates"]), entry["candidates"]
    assert (run_dir / "baseline" / "s1" / "vote" / "eval_answer.json").is_file()
    assert (run_dir / "s1" / "graded" / "team-a-analysis" / "eval_answer.json").is_file()


def test_the_referee_gets_each_answer_under_the_studys_answer_file(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "eval_answer.json", wrapped=False)
    seen = {}

    def script(agent, brief, ws):
        if "# Your job: referee" in brief:
            seen["files"] = sorted(p.name for p in (ws / "candidates" / "X").iterdir())
            assert "`eval_answer.json`" in brief
            return {"pick": "X", "reason": "test", "test": "t", "own_answer": None}
        if "# Your job: director" in brief:
            return {"pass_down": [], "questions": [], "verdict": "v"}
        if "# Your job: the lab's library" in brief:
            return {"pitfalls": []}
        value = 1.0 if agent.id.endswith("team-a") else 2.0
        (ws / "eval_answer.json").write_text(json.dumps({"effect": value, "label": "x"}))
        return {"answer_file": "eval_answer.json", "method": "m"}

    lab, _, _, run_dir = make_lab(tmp_path, adapter, MockBackend(script))
    out = lab.run(adapter.studies()[:1])
    assert out["s1"]["status"] == "refereed"
    assert seen["files"] == ["eval_answer.json", "method.json"]
    assert (run_dir / "s1" / "final" / "eval_answer.json").is_file()


def test_a_timed_out_agent_with_an_answer_file_is_salvaged(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "eval_answer.json", wrapped=False)

    class TimesOut:
        def run(self, agent, brief, workspace, *, readable=(), env=None, timeout_s=1800):
            workspace = pathlib.Path(workspace)
            if "# Your job: analyst" in brief:
                (workspace / "eval_answer.json").write_text(json.dumps({"effect": 1.0, "label": "x"}))
                return AgentResult(ok=False, result={}, seconds=1.0, error=f"timed out after {timeout_s} s")
            return AgentResult(ok=True, result={"questions": [], "verdict": "v"}, seconds=0.1)

    lab, log, _, run_dir = make_lab(tmp_path, adapter, TimesOut(), protocol.Config(max_agents=2, library=False))
    out = lab.run(adapter.studies()[:1])
    assert out["s1"]["status"] == "replicated"
    assert len(log.events("candidate.salvaged")) == 2
    assert (run_dir / "s1" / "final" / "eval_answer.json").is_file()


# ----- 4. one library department per study -----

def test_each_study_has_its_own_library_department_with_exa(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "answer.json", wrapped=True)
    built = {}

    def gateway_factory(org, memory, log, **kwargs):
        from lab.gateway import Gateway
        built.update(kwargs)
        return Gateway(org, memory, log, **kwargs)

    def script(agent, brief, ws):
        if "# Your job: the lab's library" in brief:
            return {"pitfalls": []}
        if "# Your job: director, briefing the teams" in brief:
            built.setdefault("relay_briefs", {})[ws.parent.name] = brief
            return {"pass_down": []}
        (ws / "answer.json").write_text(json.dumps({"answer": {"effect": 1.0, "label": "x"}}))
        return {"answer_file": "answer.json", "questions": [], "verdict": "v"}

    lab, log, memory, run_dir = make_lab(tmp_path, adapter, MockBackend(script), gateway_factory=gateway_factory)
    lab.run(adapter.studies())
    org = lab.org
    assert org.agent("s1-library").department == "s1-library"
    assert org.agent("s2-library").department == "s2-library"
    assert "library" not in org.departments
    assert sorted(built["exa_departments"]) == ["s1-library", "s2-library"]
    for sid in ("s1", "s2"):
        brief = built["relay_briefs"][sid]
        assert f"`{sid}-library`" in brief and "use only facts from" in brief
    # a library fact stays in its study: s2's library does not read s1's
    memory.write("s1-library", "PITFALL for s1 only: the batch column matters.", "pitfall")
    assert any("s1 only" in f.text for f in memory.recall("s1-library", "pitfall"))
    assert not any("s1 only" in f.text for f in memory.recall("s2-library", "pitfall"))
    assert any("s1 only" in f.text for f in memory.recall("director", "pitfall"))


def test_director_question_prompts_name_this_studys_team_departments(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "answer.json", wrapped=True)
    backend = MockBackend(run.mock_script(adapter))
    lab, _, _, _ = make_lab(tmp_path, adapter, backend)
    lab.run(adapter.studies()[:1])
    crossexam = next(c.brief for c in backend.calls if "# Your job: director, after the teams disagreed" in c.brief)
    assert "use only facts from s1-team-a, s1-team-b" in crossexam
    template = (protocol.PROMPTS / "director_challenge.md").read_text()
    assert "use only facts from {team_departments}" in template


# ----- 3. per-study sandbox specs on Modal -----

SPEC = {"packages": ["scanpy==1.10.4", "pandas==2.2.3"], "cpu": 4.0, "memory_mb": 16384,
        "volumes": [{"name": "summit-scbench-data", "sub_path": "/sc01", "mount": "/scbench-data"}],
        "links": {"data": "/scbench-data"}, "workspace_alias": "/workspace", "package_index": False}


def test_spec_is_found_from_the_agent_id_or_the_workspace(tmp_path):
    other = dict(SPEC, cpu=8.0)
    backend = bm.ModalBackend(specs={"sc01": SPEC, "sc10": other})
    runs = tmp_path / "run"
    assert backend.spec_for(Agent("sc01-team-a", WORKER, "sc01-team-a", "opus"), runs / "sc01" / "team-a") is SPEC
    assert backend.spec_for(Agent("sc10-library", WORKER, "sc10-library", "opus"), runs / "sc10" / "library") is other
    assert backend.spec_for(Agent("baseline-sc01-2", WORKER, "baseline", "opus"),
                            runs / "baseline" / "sc01" / "attempt-2") is SPEC
    assert backend.spec_for(Agent("director", DIRECTOR, "director", "fable"), runs / "sc10" / "director-challenge") is other
    assert backend.spec_for(Agent("gb01-team-a", WORKER, "gb01-team-a", "opus"), runs / "gb01" / "team-a") == {}
    assert bm.ModalBackend().spec_for(Agent("sc01-team-a", WORKER, "d", "opus"), runs / "sc01" / "team-a") == {}


def test_spec_sets_resources_and_closes_the_package_index():
    backend = bm.ModalBackend(specs={"sc01": SPEC})
    env = {"LAB_GATEWAY_URL": "https://a-ta-xyz.w.modal.host"}
    opts = backend.sandbox_options(1800, env, SPEC)
    assert opts["cpu"] == (4.0, 4.0) and opts["memory"] == (16384, 16384)
    assert opts["outbound_domain_allowlist"] == ["api.anthropic.com", "a-ta-xyz.w.modal.host"]
    assert not set(bm.PACKAGE_INDEX_HOSTS) & set(backend.sandbox_options(60, None, SPEC)["outbound_domain_allowlist"])
    default = backend.sandbox_options(1800, env)
    assert default["cpu"] == 2.0 and default["memory"] == 4096
    assert default["outbound_domain_allowlist"] == ["api.anthropic.com", "pypi.org", "files.pythonhosted.org",
                                                    "a-ta-xyz.w.modal.host"]
    assert backend.sandbox_options(60, None, {"package_index": True})["outbound_domain_allowlist"] == bm.AGENT_HOSTS


def test_each_package_set_gets_its_own_image_name():
    default = bm.image_name()
    assert default.startswith(bm.IMAGE_NAME + ":")
    named = bm.image_name(SPEC["packages"])
    assert named.startswith(bm.IMAGE_NAME + "-") and named.split(":")[0] != default.split(":")[0]
    assert bm.image_name(list(reversed(SPEC["packages"]))).split(":")[0] == named.split(":")[0]
    assert bm.image_name(["scanpy==1.10.3"]).split(":")[0] != named.split(":")[0]
    assert bm.image_name(SPEC["packages"]) == named                       # stable, so it is built once
    assert bm.image_inputs(SPEC["packages"]) == bm.TOOL_FILES             # no GeneBench data in it
    assert len(bm.image_inputs()) > len(bm.TOOL_FILES)


def test_link_script_makes_links_and_the_workspace_alias(tmp_path):
    ws = tmp_path / "run" / "sc01" / "team-a"
    script = bm.link_script(ws, SPEC)
    assert script.startswith("set -e") and f"ln -sfn /scbench-data {ws}/data" in script
    assert f"ln -sfn {ws} /workspace" in script and "readlink /workspace" in script
    assert bm.link_script(ws, {}) is None
    script = bm.link_script(ws, {"links": {"a.h5ad": "/scbench-data/a.h5ad"}})
    assert f"ln -sfn /scbench-data/a.h5ad {ws}/a.h5ad" in script and "/workspace" not in script
    for bad in ("../x", "a/b", "", ".."):
        with pytest.raises(ValueError):
            bm.link_script(ws, {"links": {bad: "/scbench-data"}})


def test_readable_paths_may_be_under_a_specs_mount_or_alias(tmp_path):
    extra = bm.ModalBackend.spec_paths(SPEC)
    assert extra == [pathlib.Path("/scbench-data"), pathlib.Path("/workspace")]
    ok = bm.ModalBackend.check_readable([pathlib.Path("/workspace/data"), "/scbench-data", ROOT / "lab" / "tools"],
                                        tmp_path, extra)
    assert ok == [pathlib.Path("/workspace/data"), pathlib.Path("/scbench-data"), ROOT / "lab" / "tools"]
    for bad in ("/workspace/data", "/scbench-data", "/etc"):
        with pytest.raises(ValueError):
            bm.ModalBackend.check_readable([bad], tmp_path)
    with pytest.raises(ValueError):
        bm.ModalBackend.check_readable([ROOT / "data" / "scbench" / "sealed"], tmp_path, extra)


class FakeModal(types.ModuleType):
    """Just enough of the `modal` module for create_sandbox: records what it is given."""

    def __init__(self):
        super().__init__("modal")
        made = self.made = {}

        class Volume:
            def __init__(self, name):
                self.name, self.options = name, None

            @classmethod
            def from_name(cls, name):
                return cls(name)

            def with_mount_options(self, **options):
                self.options = options
                return self

        class Secret:
            @staticmethod
            def from_name(name):
                return ("secret", name)

        class Sandbox:
            @staticmethod
            def create(*args, **kwargs):
                made.update(kwargs, args=args)
                return types.SimpleNamespace(object_id="sb-1")

        self.Volume, self.Secret, self.Sandbox = Volume, Secret, Sandbox


def test_create_sandbox_mounts_volumes_read_only_with_the_specs_image(monkeypatch):
    fake = FakeModal()
    monkeypatch.setitem(sys.modules, "modal", fake)
    monkeypatch.setattr(bm, "modal_app", lambda: "app")
    monkeypatch.setattr(bm, "agent_image", lambda build=True, packages=(): ("image", tuple(packages)))
    backend = bm.ModalBackend(secret_name=None, specs={"sc01": SPEC})
    backend.create_sandbox(600, None, SPEC)
    made = fake.made
    assert made["image"] == ("image", tuple(SPEC["packages"]))
    volume = made["volumes"]["/scbench-data"]
    assert volume.name == "summit-scbench-data" and volume.options == {"read_only": True, "sub_path": "/sc01"}
    assert made["cpu"] == (4.0, 4.0) and made["memory"] == (16384, 16384)
    assert "pypi.org" not in made["outbound_domain_allowlist"]
    backend.create_sandbox(600, None, {})
    assert fake.made["image"] == ("image", ()) and fake.made["volumes"] == {} and fake.made["cpu"] == 2.0
    assert backend.live() == ["sb-1"]


def test_make_links_runs_the_link_script_in_the_sandbox(tmp_path):
    ran = []

    class Proc:
        stdout = types.SimpleNamespace(read=lambda: "")
        stderr = types.SimpleNamespace(read=lambda: "")

        def wait(self):
            return 0

    class Sandbox:
        def exec(self, *args, **kwargs):
            ran.append(args)
            return Proc()

    bm.ModalBackend().make_links(Sandbox(), tmp_path, SPEC)
    assert ran and ran[0][:2] == ("bash", "-c") and "ln -sfn /scbench-data" in ran[0][2]
    ran.clear()
    bm.ModalBackend().make_links(Sandbox(), tmp_path, {})
    assert ran == []


def test_make_backend_passes_each_studys_spec(tmp_path):
    adapter = FakeAdapter(tmp_path / "bench", "eval_answer.json", wrapped=False)
    adapter.modal_spec = lambda study: {"cpu": 4.0, "study": study.id}
    backend = run.make_backend("modal", adapter, adapter.studies())
    assert backend.specs == {"s1": {"cpu": 4.0, "study": "s1"}, "s2": {"cpu": 4.0, "study": "s2"}}
    assert run.make_backend("modal", FakeAdapter(tmp_path / "b2", "answer.json", True), []).specs == {}


# ----- scBench itself (needs the staged benchmark) -----

scbench_staged = pytest.mark.skipif(
    not (ROOT / "data" / "scbench" / "public").exists() or not (ROOT / "data" / "scbench" / "sealed" / "summit_mapping.json").exists(),
    reason="scBench not staged")


def test_scbench_is_a_known_benchmark():
    assert run.load_adapter("scbench").name == "scbench-public"


@scbench_staged
def test_scbench_studies_see_their_data_inside_the_sandbox():
    from lab.adapters import scbench
    adapter = scbench.ScBench()
    studies = {s.id: s for s in adapter.studies()}
    brief_pins = ["scanpy==1.10.4", "anndata==0.11.1", "h5py==3.12.1", "numpy==1.26.4", "pandas==2.2.3",
                  "scipy==1.14.1", "scikit-learn==1.5.2", "leidenalg==0.10.2", "igraph==0.11.8", "umap-learn==0.5.7"]
    for sid, study in studies.items():
        spec = adapter.modal_spec(study)
        assert all(p in spec["packages"] for p in brief_pins) and any(p.startswith("matplotlib") for p in spec["packages"])
        assert spec["cpu"] == 4.0 and spec["memory_mb"] == 16384 and spec["package_index"] is False
        assert spec["volumes"] == [{"name": "summit-scbench-data", "sub_path": f"/{sid}", "mount": "/scbench-data"}]
        files = [f["name"] for f in adapter.data_manifest(study)["files"]]
        if sid in [f"sc{i:02d}" for i in range(1, 7)]:
            assert study.data_dir == pathlib.Path("/workspace/data") and "/workspace/data" in study.statement
            assert spec["links"] == {"data": "/scbench-data"} and spec["workspace_alias"] == "/workspace"
        else:
            assert study.data_dir == pathlib.Path("/scbench-data") and spec["workspace_alias"] is None
            assert spec["links"] == {name: f"/scbench-data/{name}" for name in files}
            assert all(name in study.statement for name in files)
        backend = bm.ModalBackend(specs={sid: spec})
        backend.check_readable([study.data_dir, ROOT / "lab" / "tools"], pathlib.Path("/tmp/x"), backend.spec_paths(spec))


@scbench_staged
def test_missing_data_names_the_studies_and_the_command():
    from lab.adapters import scbench
    adapter = scbench.ScBench()
    studies = {s.id: s for s in adapter.studies()}
    full = {f"/{sid}": {f["name"]: f["size_bytes"] for f in adapter.data_manifest(s)["files"]} for sid, s in studies.items()}
    assert adapter.missing_data(list(studies.values()), full.get) == []
    short = dict(full, **{"/sc07": {}, "/sc02": {k: v - 1 for k, v in full["/sc02"].items()}})
    assert adapter.missing_data([studies["sc01"], studies["sc02"], studies["sc07"]], short.get) == ["sc02", "sc07"]
    assert adapter.data_command(["sc07", "sc30"]) == "modal run lab/adapters/scbench_data.py --sets canonical"
    assert adapter.data_command(["sc02"]) == "modal run lab/adapters/scbench_data.py --sets current"
    assert adapter.data_command(["sc02", "sc07"]) == "modal run lab/adapters/scbench_data.py --sets all"


@scbench_staged
def test_lab_and_baseline_stop_early_without_data_or_on_the_laptop_backend(tmp_path, monkeypatch):
    from lab.adapters import scbench
    adapter = scbench.ScBench()
    sc01 = next(s for s in adapter.studies() if s.id == "sc01")
    on_volume = {"/sc01": {f["name"]: f["size_bytes"] for f in adapter.data_manifest(sc01)["files"]}}
    monkeypatch.setattr(scbench, "volume_listing", lambda sub_path: on_volume.get(sub_path, {}))
    for command in (["lab", "--memory", "past"], ["baseline"]):
        run_dir = tmp_path / command[0]
        args = [command[0], "--benchmark", "scbench", "--studies", "sc01,sc07", "--run-dir", str(run_dir), *command[1:]]
        with pytest.raises(SystemExit) as err:
            run.main(args + ["--backend", "modal"])
        message = str(err.value)
        assert "sc07" in message and "sc01" not in message
        assert "modal run lab/adapters/scbench_data.py --sets canonical" in message
        with pytest.raises(SystemExit) as err:
            run.main(args + ["--backend", "claude"])
        assert "--backend modal" in str(err.value)
        assert not run_dir.exists()          # stopped before anything was created


@scbench_staged
def test_scbench_runs_end_to_end_with_the_mock_backend(tmp_path, monkeypatch):
    from lab import measures
    from lab.adapters import scbench
    calls = []

    def stub(set_name, grader_type, config, answer):
        calls.append(set_name)
        return {"passed": False, "score": 0.0}

    monkeypatch.setattr(scbench, "_benchmark_grader", stub)
    run_dir = tmp_path / "pilot"
    common = ["--benchmark", "scbench", "--studies", "sc01,sc07", "--run-dir", str(run_dir), "--backend", "mock"]
    assert run.main(["lab", *common, "--memory", "local"]) == 0
    assert run.main(["baseline", *common, "--attempts", "2"]) == 0
    for sid in ("sc01", "sc07"):
        final = run_dir / sid / "final" / "eval_answer.json"
        assert final.is_file() and not (run_dir / sid / "final" / "answer.json").exists()
        assert scbench.ScBench().problems(next(s for s in scbench.ScBench().studies() if s.id == sid),
                                          "eval_answer.json", final) == []
        assert (run_dir / "baseline" / sid / "attempt-2" / "eval_answer.json").is_file()
    assert run.main(["grade", "--run-dir", str(run_dir)]) == 0
    results = json.loads((run_dir / "results.json").read_text())
    assert results["benchmark"] == "scbench-public" and results["totals"]["lab_studies"] == 2
    assert {"current", "canonical"} <= set(calls)
    assert all("error" not in r for e in results["studies"].values() for r in e["candidates"]), "an answer was not graded"
    assert measures.measure(run_dir)["headline"]


# ----- 6. the benchmark's own grader, in its sealed environment -----

@scbench_staged
@pytest.mark.skipif(not (ROOT / "data" / "scbench" / "sealed" / "grader-venv" / "bin" / "python").is_file(),
                    reason="scBench grading environment not made (python3 -m lab.adapters.scbench grader-env)")
def test_real_grader_fails_a_wrong_placeholder_for_both_sets(tmp_path):
    """Runs the benchmark's own grader code. Shows only pass or fail, never a value or tolerance."""
    from lab.adapters import scbench
    adapter = scbench.ScBench()
    placeholder = {"number": -98765.4321, "string": "Z", "list": ["NOT_A_REAL_GENE_X"],
                   "mapping": {"not a cell type": 100.0}}
    for sid in ("sc01", "sc07"):          # one current-set task, one older-set task
        study = next(s for s in adapter.studies() if s.id == sid)
        folder = tmp_path / sid
        folder.mkdir()
        (folder / "eval_answer.json").write_text(json.dumps({k: placeholder[v] for k, v in adapter.schema(study).items()}))
        verdict = adapter.grade(study, folder)
        graded_and_failed = verdict.get("passed") is False and "error" not in verdict
        assert graded_and_failed, f"{sid}: the grader did not run cleanly or the placeholder passed"
    assert scbench.GRADER_ENV in scbench.GRADER_PYTHON.parents and scbench.SEALED in scbench.GRADER_ENV.parents
