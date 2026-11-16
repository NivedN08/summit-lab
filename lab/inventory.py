"""An inventory of a study's data, built by code, for the "Not verifiable" re-check (Config.recheck).

`build_inventory(data_dir, workspace, hypothesis)` lists every data file and its columns, marks
which columns and files a team's work mentions, and ranks the columns it never mentions by how
many words they share with the hypothesis.

  columns     a table's header. In cBioPortal-style tables, lines starting with `#` are comments
              (the first gives each column's display name, the second its description) and the
              first other line is the header. Tab-separated, else comma-separated.
  used        a column is used when its name appears, as a whole word in any case, in the team's
              `*.py`, `*.R` or `*.ipynb` files, or in the method or reasoning text of its
              `result.json` or answer file. A file is used when its name appears there.
  ranking     unused columns by word overlap with the hypothesis (column name, display name and
              description against the hypothesis's content words), then in file and column order.
              The top TOP_COLUMNS are returned, plus every unused file.

`render_inventory(inventory)` is the plain text the re-check brief shows. The whole inventory is
written to `recheck_inventory.json` in the team's folder.
"""

from __future__ import annotations

import json
import pathlib
import re

INVENTORY_FILE = "recheck_inventory.json"
DATA_ROOT = pathlib.Path(__file__).resolve().parents[1] / "data"   # the repository's data root
TOP_COLUMNS = 25
TABLE_SUFFIXES = {".txt", ".tsv", ".csv", ".tab", ".seg", ".maf", ".gct"}
CODE_SUFFIXES = {".py", ".r", ".ipynb"}
MAX_CODE_BYTES = 2_000_000          # a larger script or notebook is read only this far
MAX_HEADER_SCAN = 1000              # lines read looking for the header
DESCRIPTION_CHARS = 120             # a column description is cut to this in the brief
STOP_WORDS = set("""
a an and are as at be been but by can do does for from has have higher in into is it its lower more less
no not of on or than that the their there these this those to was were which with without who whose
patient patients sample samples id ids data study tumour tumours tumor tumors group groups
""".split())


def hypothesis_text(statement: str) -> str:
    """The hypothesis of a BioDSA-style task: the text under its "# Hypothesis" heading, else the whole statement."""
    match = re.search(r"^#\s*Hypothesis\s*$(.*?)(?=^#\s|\Z)", statement, re.MULTILINE | re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else statement).strip()


def words(text: str) -> list[str]:
    """Lower-case content words: camelCase and snake_case split, stop words and bare numbers dropped."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(text))
    return [w for w in re.findall(r"[a-z0-9]+", text.lower())
            if len(w) >= 2 and w not in STOP_WORDS and not w.isdigit()]


def overlap(column_words: list[str], hypothesis_words: set[str]) -> int:
    """How many of a column's distinct words the hypothesis has (equal, or the same first five letters)."""
    stems = {h[:5] for h in hypothesis_words if len(h) >= 5}
    return sum(1 for w in set(column_words) if w in hypothesis_words or (len(w) >= 5 and w[:5] in stems))


def read_header(path: pathlib.Path) -> tuple[list[str], list[str], list[str]]:
    """(columns, display names, descriptions) of a table; empty lists for a file with no header."""
    comments = []
    try:
        with open(path, errors="replace") as f:
            for i, line in enumerate(f):
                if i >= MAX_HEADER_SCAN:
                    break
                line = line.rstrip("\r\n")
                if not line.strip():
                    continue
                if line.startswith("#"):
                    comments.append(line[1:])
                    continue
                sep = "\t" if "\t" in line else "," if "," in line else None
                if sep is None:     # one column, or not a table (a "key: value" metadata file)
                    name = line.strip()
                    return ([name], [""], [""]) if len(name) <= 64 and ":" not in name else ([], [], [])
                columns = [c.strip().strip('"') for c in line.split(sep)]
                parts = [c.split(sep) for c in comments[:2]]
                display = parts[0] if len(parts) >= 1 and len(parts[0]) == len(columns) else [""] * len(columns)
                desc = parts[1] if len(parts) >= 2 and len(parts[1]) == len(columns) else [""] * len(columns)
                return columns, [d.strip() for d in display], [d.strip() for d in desc]
    except OSError:
        pass
    return [], [], []


def notebook_text(path: pathlib.Path) -> str:
    """The source cells of a notebook; the raw file when it is not valid JSON."""
    raw = path.read_text(errors="replace")[:MAX_CODE_BYTES]
    try:
        cells = json.loads(raw).get("cells") or []
    except (ValueError, AttributeError):
        return raw
    return "\n".join("".join(c.get("source") or []) if isinstance(c.get("source"), list) else str(c.get("source") or "")
                     for c in cells if isinstance(c, dict))


def team_text(workspace: pathlib.Path, answer_file: str = "answer.json") -> str:
    """Everything the team's work says: its code, and the method and reasoning in result.json and the answer."""
    workspace = pathlib.Path(workspace)
    parts = []
    if workspace.is_dir():
        for path in sorted(workspace.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in CODE_SUFFIXES:
                continue
            parts.append(notebook_text(path) if path.suffix.lower() == ".ipynb"
                         else path.read_text(errors="replace")[:MAX_CODE_BYTES])
    for name in ("result.json", answer_file):
        try:
            doc = json.loads((workspace / name).read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            parts += [json.dumps(doc[k]) if not isinstance(doc[k], str) else doc[k]
                      for k in ("method", "reasoning") if k in doc]
    return "\n".join(parts)


def mentioned(name: str, text: str) -> bool:
    """Whether `name` appears in `text` as a whole word (letters, digits and _ around it do not count), in any case."""
    if not name:
        return False
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])", text, re.IGNORECASE) is not None


def data_label(data_dir: pathlib.Path) -> str:
    """How the inventory names the data folder: relative to the repository's data root when it is
    under it, else the folder's own name. Never an absolute local path."""
    path = pathlib.Path(data_dir).resolve()
    return path.relative_to(DATA_ROOT).as_posix() if path.is_relative_to(DATA_ROOT) else path.name


def build_inventory(data_dir: pathlib.Path, workspace: pathlib.Path, hypothesis: str,
                    answer_file: str = "answer.json") -> dict:
    """The inventory of `data_dir` against the team's work in `workspace`. See the module docstring."""
    data_dir = pathlib.Path(data_dir)
    text = team_text(workspace, answer_file)
    wanted = set(words(hypothesis))
    files, unused_files, unused = [], [], []
    total = 0
    for path in sorted(p for p in data_dir.rglob("*") if p.is_file() and not p.name.startswith(".")):
        rel = path.relative_to(data_dir).as_posix()
        columns, display, desc = read_header(path) if path.suffix.lower() in TABLE_SUFFIXES else ([], [], [])
        used_cols = [c for c in columns if mentioned(c, text)]
        file_used = mentioned(path.name, text) or mentioned(path.stem, text)
        files.append({"file": rel, "columns": columns, "used_columns": used_cols,
                      "mentioned": file_used or bool(used_cols)})
        if not files[-1]["mentioned"]:
            unused_files.append(rel)
        for i, column in enumerate(columns):
            total += 1
            if column in used_cols:
                continue
            score = overlap(words(f"{column} {display[i]} {desc[i]}"), wanted)
            unused.append({"file": rel, "column": column, "display": display[i], "description": desc[i],
                           "overlap": score})
    ranked = sorted(enumerate(unused), key=lambda pair: (-pair[1]["overlap"], pair[0]))
    return {"data_dir": data_label(data_dir), "hypothesis": hypothesis, "files": files, "unused_files": unused_files,
            "columns_total": total, "unused_columns_total": len(unused),
            "unused_columns": [item for _, item in ranked[:TOP_COLUMNS]]}


def render_inventory(inventory: dict) -> str:
    """The inventory as the plain text of the re-check brief."""
    lines = ["Data files, with how many of their columns your work mentions:"]
    for f in inventory["files"]:
        cols = f"{len(f['columns'])} columns, {len(f['used_columns'])} mentioned" if f["columns"] else "not a table"
        lines.append(f"- `{f['file']}`: {cols}" + ("" if f["mentioned"] else "; your work never names this file"))
    if inventory["unused_files"]:
        lines += ["", "Files your work never names: " + ", ".join(f"`{f}`" for f in inventory["unused_files"]) + "."]
    columns = inventory["unused_columns"]
    if columns:
        lines += ["", f"Columns your work never mentions, closest to the claim's words first "
                      f"({len(columns)} of {inventory['unused_columns_total']}):"]
        for c in columns:
            about = " - ".join(x for x in (c["display"], c["description"][:DESCRIPTION_CHARS]) if x and x != c["column"])
            lines.append(f"- `{c['file']}` `{c['column']}`" + (f": {about}" if about else ""))
    else:
        lines += ["", "Your work mentions every column of every table."]
    return "\n".join(lines)
