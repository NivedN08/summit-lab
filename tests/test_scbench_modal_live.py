"""Live checks of the scBench sandbox on Modal. Run only with LAB_MODAL=1 (the `modal` client logged in):

    LAB_MODAL=1 python3 -m pytest tests/test_scbench_modal_live.py -v

No agent is started: one short sandbox, with no secret, runs plain shell commands and is
terminated. Needs the staged benchmark and sc01's data on the Volume. Prints no data values.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LAB_MODAL") != "1", reason="live Modal test; set LAB_MODAL=1")

from lab import backend_modal as bm  # noqa: E402
from lab.adapters import scbench  # noqa: E402

WS = pathlib.Path("/tmp/lab-live/sc01/team-a")      # stands in for an agent's working folder


@pytest.fixture(scope="module")
def sc01():
    adapter = scbench.ScBench()
    study = next(s for s in adapter.studies() if s.id == "sc01")
    return adapter, study, adapter.modal_spec(study)


@pytest.fixture(scope="module")
def sandbox(sc01):
    """One sandbox with the sc01 spec, as ModalBackend.run makes it, minus the Claude secret."""
    adapter, study, spec = sc01
    backend = bm.ModalBackend(secret_name=None, specs={"sc01": spec})
    sb = backend.create_sandbox(300, None, spec)
    try:
        sb.filesystem.make_directory(str(WS))
        backend.make_links(sb, WS, spec)
        yield sb
    finally:
        backend.shutdown()           # terminates the sandbox
        assert backend.live() == []


def sh(sb, script: str, timeout: int = 120) -> tuple[int, str]:
    p = sb.exec("bash", "-c", script, timeout=timeout)
    out = p.stdout.read() + p.stderr.read()
    return p.wait(), out


def test_image_builds_and_imports_scanpy(sandbox, sc01):
    _, _, spec = sc01
    code, out = sh(sandbox, "python3 -c 'import scanpy, anndata, h5py, numpy, pandas, scipy, sklearn, leidenalg, "
                            "igraph, umap, matplotlib; print(scanpy.__version__, anndata.__version__, numpy.__version__, "
                            "pandas.__version__, scipy.__version__, sklearn.__version__)' && claude --version")
    assert code == 0, out
    assert "1.10.4 0.11.1 1.26.4 2.2.3 1.14.1 1.5.2" in out and bm.CLAUDE_CODE_VERSION in out
    assert bm.image_name(spec["packages"]).startswith(bm.IMAGE_NAME + "-")


def test_data_is_read_only_where_the_task_text_says(sandbox, sc01):
    adapter, study, _ = sc01
    assert str(study.data_dir) == "/workspace/data"
    for f in adapter.data_manifest(study)["files"]:
        path = f"{study.data_dir}/{f['name']}"
        code, out = sh(sandbox, f"stat -L -c %s {path}")
        assert code == 0 and int(out.split()[0]) == f["size_bytes"], out
        code, out = sh(sandbox, f"python3 -c 'import anndata; anndata.read_h5ad(\"{path}\", backed=\"r\"); print(\"opened\")'")
        assert code == 0 and "opened" in out, out[-500:]
    for target in ("/workspace/data/new.txt", "/scbench-data/new.txt"):
        code, out = sh(sandbox, f"touch {target}")
        assert code != 0 and "Read-only" in out, out
    code, out = sh(sandbox, f"echo '{{}}' > /workspace/eval_answer.json && test -f {WS}/eval_answer.json "
                            f"&& test \"$(readlink /workspace)\" = {WS}")
    assert code == 0, out


RESOURCES = r"""
import os, pathlib
v1 = pathlib.Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
if v1.exists():
    quota, period = int(v1.read_text()), int(pathlib.Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
else:
    quota, period = map(int, pathlib.Path("/sys/fs/cgroup/cpu.max").read_text().split())
mem_kb = next(int(line.split()[1]) for line in open("/proc/meminfo") if line.startswith("MemTotal"))
print(len(os.sched_getaffinity(0)), quota / period, mem_kb)
"""


def test_four_cpus_and_sixteen_gigabytes(sandbox):
    code, out = sh(sandbox, f"python3 -c '{RESOURCES}'")
    assert code == 0, out
    cpus, cpu_limit, mem_kb = out.split()
    assert int(cpus) == 4 and float(cpu_limit) == 4.0, out
    assert int(mem_kb) == 16 * 1024 * 1024, out


def test_no_package_index_but_the_model_api(sandbox):
    def status(url):
        _, out = sh(sandbox, f"curl -s -o /dev/null -m 15 -w '%{{http_code}}' {url}; echo \" rc=$?\"")
        return out.strip()

    assert status("https://pypi.org/simple/numpy/").startswith("000"), "pypi.org was reachable"
    assert status("https://files.pythonhosted.org/").startswith("000"), "files.pythonhosted.org was reachable"
    assert status("https://huggingface.co/").startswith("000"), "the data mirror was reachable"
    assert not status("https://api.anthropic.com/v1/messages").startswith("000")
    code, out = sh(sandbox, "pip download --no-deps -d /tmp/pkg six 2>&1 | tail -1")
    assert "No matching distribution" in out or "rror" in out, out


class ScriptedBackend(bm.ModalBackend):
    """ModalBackend with a bash script in place of the Claude CLI, and no secret."""

    def __init__(self, script: str, specs: dict):
        super().__init__(secret_name=None, specs=specs)
        self.script = script

    def command(self, agent, workspace, readable=(), resume=None):
        return ["bash", "-c", self.script]


def test_a_run_with_the_spec_brings_the_answer_back_but_not_the_data(tmp_path, sc01):
    from lab.contracts import WORKER, Agent
    _, study, spec = sc01
    ws = tmp_path / "sc01" / "team-a"
    ws.mkdir(parents=True)
    backend = ScriptedBackend("ls /workspace/data > seen.txt; echo '{\"x\": 1}' > /workspace/eval_answer.json; "
                              "echo '{\"answer_file\": \"eval_answer.json\"}' > result.json", {"sc01": spec})
    result = backend.run(Agent("sc01-team-a", WORKER, "sc01-team-a", "haiku"), "brief", ws,
                         readable=[study.data_dir, bm.ROOT / "lab" / "tools"], timeout_s=120)
    assert result.ok, result.error
    assert (ws / "eval_answer.json").read_text().strip() == '{"x": 1}'
    names = [f["name"] for f in scbench.ScBench().data_manifest(study)["files"]]
    assert (ws / "seen.txt").read_text().split() == names
    assert not (ws / "data").exists() and not any(p.suffix == ".h5ad" for p in ws.rglob("*"))
    assert backend.live() == []


def test_missing_data_check_reads_the_volume():
    adapter = scbench.ScBench()
    studies = {s.id: s for s in adapter.studies()}
    assert adapter.missing_data([studies[f"sc{i:02d}"] for i in range(1, 7)]) == []
    assert adapter.missing_data([studies["sc07"]]) == ["sc07"]
