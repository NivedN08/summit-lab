"""Live check of the BioDSA-1K sandbox on Modal. Run only with LAB_MODAL=1 (the `modal` client logged in):

    LAB_MODAL=1 python3 -m pytest tests/test_biodsa_modal_live.py -v

No agent is started: one short sandbox, with no secret, runs plain shell commands and is
terminated. Needs the staged benchmark and bd01's tables on the Volume. Prints no data values.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LAB_MODAL") != "1", reason="live Modal test; set LAB_MODAL=1")

from lab import backend_modal as bm  # noqa: E402
from lab.adapters import biodsa  # noqa: E402

WS = pathlib.Path("/tmp/lab-live/bd01/team-a")      # stands in for an agent's working folder


@pytest.fixture(scope="module")
def bd01():
    adapter = biodsa.BioDSA()
    study = next(s for s in adapter.studies() if s.id == "bd01")
    return adapter, study, adapter.modal_spec(study)


@pytest.fixture(scope="module")
def sandbox(bd01):
    """One sandbox with the bd01 spec, as ModalBackend.run makes it, minus the Claude secret."""
    _, _, spec = bd01
    backend = bm.ModalBackend(secret_name=None, specs={"bd01": spec})
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


def test_tables_are_read_only_at_the_stated_path(sandbox, bd01):
    adapter, study, _ = bd01
    assert str(study.data_dir) == "/biodsa-data"
    tables = adapter.tables(study)
    code, out = sh(sandbox, f"cd {study.data_dir} && for f in *; do echo \"$f $(stat -c %s \"$f\")\"; done")
    assert code == 0, out
    seen = dict(line.rsplit(" ", 1) for line in out.strip().splitlines())
    assert {k: int(v) for k, v in seen.items()} == tables
    code, out = sh(sandbox, f"touch {study.data_dir}/new.txt")
    assert code != 0 and "Read-only" in out, out
    first = sorted(tables)[0]
    code, out = sh(sandbox, f"python3 -c 'import pandas; pandas.read_csv(\"{study.data_dir}/{first}\", sep=\"\\t\", "
                            f"comment=\"#\", nrows=5); print(\"opened\")'")
    assert code == 0 and "opened" in out, out[-500:]


def test_survival_and_statistics_packages_import(sandbox, bd01):
    _, _, spec = bd01
    code, out = sh(sandbox, "python3 -c 'import lifelines, pandas, numpy, scipy, statsmodels, sklearn; "
                            "print(lifelines.__version__)' && claude --version")
    assert code == 0, out
    assert spec["packages"][0].split("==")[1] in out and bm.CLAUDE_CODE_VERSION in out


def test_resources_match_the_spec(sandbox, bd01):
    _, _, spec = bd01
    script = ("import os; mem = next(int(l.split()[1]) for l in open('/proc/meminfo') if l.startswith('MemTotal')); "
              "print(len(os.sched_getaffinity(0)), mem)")
    code, out = sh(sandbox, f"python3 -c \"{script}\"")
    assert code == 0, out
    cpus, mem_kb = map(int, out.split())
    assert cpus == int(spec["cpu"]) and mem_kb == spec["memory_mb"] * 1024, out
