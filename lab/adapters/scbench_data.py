"""Put scBench's .h5ad data files on the Modal Volume `summit-scbench-data`.

Layout: one folder per neutral task id, holding that task's files under the names the task
text uses, for example /sc01/illumina_pipseeker.h5ad. An agent's sandbox mounts only its own
task's folder, read-only, at the place the task text names:

    volume.with_mount_options(read_only=True, sub_path="/sc01")  ->  /workspace/data

Files come straight from their source: the current set from Latch storage (a signed link that
needs no account), the canonical set from the Hugging Face mirror Inspect Evals pins. A file
several tasks share is downloaded once and copied inside the Volume. Files already present
with the right size are skipped, so the command can be re-run.

Needs the public side staged first (python3 -m lab.adapters.scbench stage). Run from the repo root:

    modal run lab/adapters/scbench_data.py                    # current set, 6 files, about 2.0 GB
    modal run lab/adapters/scbench_data.py --sets canonical   # the 30-task set, 18 files, 17.2 GB
    modal run lab/adapters/scbench_data.py --sets all

Imports only the standard library and modal, because Modal imports this file inside the container.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import pathlib
import shutil
import time
import urllib.request

import modal

VOLUME_NAME = "summit-scbench-data"
CURRENT_IDS = [f"sc{i:02d}" for i in range(1, 7)]          # sc01-sc06 are the current set
LATCH_SIGNED_URL = "https://nucleus.latch.bio/ldata/get-signed-url"
MOUNT = "/vol"
CHUNK = 8 * 1024 * 1024

app = modal.App("summit-scbench-data")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.12")


def plan(sets: str = "current") -> list[dict]:
    """One row per (task, file) from the public data manifests: where to put it and where it comes from.
    Runs on the laptop only (inside the container this file sits at /root, away from the repo)."""
    PUBLIC = pathlib.Path(__file__).resolve().parents[2] / "data" / "scbench" / "public"
    if not PUBLIC.exists():
        raise SystemExit("stage the public side first: python3 -m lab.adapters.scbench stage")
    rows = []
    for folder in sorted(PUBLIC.iterdir()):
        manifest_path = folder / "data_manifest.json"
        if not manifest_path.exists():
            continue
        in_current = folder.name in CURRENT_IDS
        if (sets == "current" and not in_current) or (sets == "canonical" and in_current):
            continue
        for f in json.loads(manifest_path.read_text())["files"]:
            rows.append({"path": f["volume_path"], "url": f["url"], "source": f["source"],
                         "size_bytes": f["size_bytes"], "sha256": f.get("sha256")})
    return rows


def _download_url(row: dict) -> str:
    if row["source"] != "latch":
        return row["url"]
    body = json.dumps({"path": row["url"], "egress_event_data": {"purpose": json.dumps({"method": "latch-cli"})}})
    request = urllib.request.Request(LATCH_SIGNED_URL, data=body.encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["data"]["url"]


def _fetch(row: dict, target: pathlib.Path) -> dict:
    """Stream one file to `target`, checking size and (when known) sha256. Writes to a temp name first."""
    started = time.time()
    partial = target.with_name(target.name + ".partial")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(_download_url(row), timeout=120) as response, open(partial, "wb") as out:
        while chunk := response.read(CHUNK):
            out.write(chunk)
            digest.update(chunk)
    size = partial.stat().st_size
    if size != row["size_bytes"]:
        partial.unlink()
        raise RuntimeError(f"{row['path']}: got {size} bytes, expected {row['size_bytes']}")
    if row["sha256"] and digest.hexdigest() != row["sha256"]:
        partial.unlink()
        raise RuntimeError(f"{row['path']}: sha256 mismatch")
    os.replace(partial, target)
    return {"path": row["path"], "bytes": size, "seconds": round(time.time() - started, 1), "how": "downloaded"}


@app.function(image=image, volumes={MOUNT: volume}, timeout=3 * 3600, cpu=4.0, memory=4096)
def download(rows: list[dict], workers: int = 4) -> list[dict]:
    """Inside Modal: fetch each distinct source once, copy it to every task folder that needs it, commit."""
    root = pathlib.Path(MOUNT)
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(row["url"], []).append(row)

    def where(row: dict) -> pathlib.Path:
        return root / row["path"].lstrip("/")

    def present(row: dict) -> bool:
        return where(row).is_file() and where(row).stat().st_size == row["size_bytes"]

    def one_source(group: list[dict]) -> list[dict]:
        report = []
        source = next((r for r in group if present(r)), None)
        if source is None:                       # nothing on the volume yet: download into the first folder
            source = group[0]
            report.append(_fetch(source, where(source)))
        for row in group:
            if row is source and report:
                continue                          # just downloaded
            if present(row):
                report.append({"path": row["path"], "bytes": row["size_bytes"], "seconds": 0, "how": "present"})
                continue
            started = time.time()
            where(row).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(where(source), where(row))
            report.append({"path": row["path"], "bytes": row["size_bytes"],
                           "seconds": round(time.time() - started, 1), "how": "copied"})
        return report

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(one_source, by_source.values()):
            results.extend(part)
    volume.commit()
    return results


@app.local_entrypoint()
def main(sets: str = "current"):
    if sets not in ("current", "canonical", "all"):
        raise SystemExit("--sets must be current, canonical or all")
    rows = plan(sets)
    distinct = {r["url"]: r["size_bytes"] for r in rows}
    print(f"{len(rows)} task files, {len(distinct)} distinct downloads, "
          f"{sum(distinct.values()) / 1e9:.2f} GB to download, {sum(r['size_bytes'] for r in rows) / 1e9:.2f} GB on the volume")
    started = time.time()
    report = download.remote(rows)
    for r in sorted(report, key=lambda r: r["path"]):
        rate = f"{r['bytes'] / r['seconds'] / 1e6:.0f} MB/s" if r["seconds"] else ""
        print(f"  {r['how']:10s} {r['path']:48s} {r['bytes'] / 1e6:9.1f} MB {r['seconds']:7.1f} s {rate}")
    print(f"done in {time.time() - started:.0f} s; volume {VOLUME_NAME}")
