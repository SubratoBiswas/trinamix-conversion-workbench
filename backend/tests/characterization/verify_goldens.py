"""Golden-file characterization harness — Phase 0 of the clean-architecture migration.

Formalises the manual "regenerate, download, diff against the reference workbook" loop
into a reusable, automatable comparison. Every refactoring slice must keep the generated
artifacts byte-identical; this is the gate that proves it.

Compares two FBDI artifacts sheet-by-sheet, cell-by-cell:
  * .xlsm / .xlsx  — every worksheet, every populated cell (values, data_only)
  * .zip           — every positional CSV member (headerless FBDI export)
  * .csv           — a single sheet

Usage
-----
    python -m tests.characterization.verify_goldens GOLDEN CANDIDATE
    # exit 0 = identical, exit 1 = drift (report printed)

    from tests.characterization.verify_goldens import compare_artifacts
    report = compare_artifacts("golden.xlsm", "candidate.xlsm")
    assert report.identical, report.summary()

Deliberately dependency-light: openpyxl for workbooks, stdlib csv/zipfile otherwise.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

_MAX_CELL_DIFFS = 50   # cap the printed detail; the counts are always complete


def _norm(v) -> str:
    """A blank-insensitive string view of a cell, so None vs "" is not a false diff."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "nat", "<na>") else s


def _sheets_from_xlsx(path: Path) -> dict[str, list[list[str]]]:
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: dict[str, list[list[str]]] = {}
    for ws in wb.worksheets:
        out[ws.title] = [[_norm(c) for c in row] for row in ws.iter_rows(values_only=True)]
    return out


def _sheets_from_zip(path: Path) -> dict[str, list[list[str]]]:
    out: dict[str, list[list[str]]] = {}
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            text = z.read(name).decode("utf-8-sig", errors="replace")
            out[name] = [[_norm(c) for c in row] for row in csv.reader(io.StringIO(text))]
    return out


def _sheets_from_csv(path: Path) -> dict[str, list[list[str]]]:
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        return {path.name: [[_norm(c) for c in row] for row in csv.reader(f)]}


def _load(path: str | Path) -> dict[str, list[list[str]]]:
    p = Path(path)
    suf = p.suffix.lower()
    if suf in (".xlsm", ".xlsx"):
        return _sheets_from_xlsx(p)
    if suf == ".zip":
        return _sheets_from_zip(p)
    if suf in (".csv", ".tsv", ".txt"):
        return _sheets_from_csv(p)
    raise ValueError(f"unsupported artifact type: {p.suffix} ({p})")


@dataclass
class Report:
    only_in_golden: list[str] = field(default_factory=list)
    only_in_candidate: list[str] = field(default_factory=list)
    rowcount_diffs: list[tuple[str, int, int]] = field(default_factory=list)   # sheet, g, c
    cell_diffs: list[tuple[str, int, int, str, str]] = field(default_factory=list)
    cell_diff_total: int = 0

    @property
    def identical(self) -> bool:
        return not (self.only_in_golden or self.only_in_candidate
                    or self.rowcount_diffs or self.cell_diff_total)

    def summary(self) -> str:
        if self.identical:
            return "IDENTICAL — no drift."
        lines = ["DRIFT DETECTED:"]
        if self.only_in_golden:
            lines.append(f"  sheets only in golden:    {self.only_in_golden}")
        if self.only_in_candidate:
            lines.append(f"  sheets only in candidate: {self.only_in_candidate}")
        for sh, g, c in self.rowcount_diffs:
            lines.append(f"  row count [{sh}]: golden={g} candidate={c}")
        if self.cell_diff_total:
            lines.append(f"  cell value diffs: {self.cell_diff_total} "
                         f"(showing up to {_MAX_CELL_DIFFS})")
            for sh, r, col, g, c in self.cell_diffs[:_MAX_CELL_DIFFS]:
                lines.append(f"    [{sh}] row {r} col {col}: golden={g!r} candidate={c!r}")
        return "\n".join(lines)


def _compare_grids(g: dict, c: dict) -> Report:
    rep = Report()
    rep.only_in_golden = sorted(set(g) - set(c))
    rep.only_in_candidate = sorted(set(c) - set(g))
    for sheet in sorted(set(g) & set(c)):
        gr, cr = g[sheet], c[sheet]
        if len(gr) != len(cr):
            rep.rowcount_diffs.append((sheet, len(gr), len(cr)))
        for r in range(min(len(gr), len(cr))):
            grow, crow = gr[r], cr[r]
            for col in range(max(len(grow), len(crow))):
                gv = grow[col] if col < len(grow) else ""
                cv = crow[col] if col < len(crow) else ""
                if gv != cv:
                    rep.cell_diff_total += 1
                    if len(rep.cell_diffs) < _MAX_CELL_DIFFS:
                        rep.cell_diffs.append((sheet, r + 1, col, gv, cv))
    return rep


def compare_artifacts(golden: str | Path, candidate: str | Path) -> Report:
    """Compare two live artifacts (xlsm/zip/csv)."""
    return _compare_grids(_load(golden), _load(candidate))


# ── In-repo golden storage ──────────────────────────────────────────────────────
# A golden is stored as a gzipped JSON of the normalised cell grid — a few hundred KB,
# diff-friendly and deterministic, instead of committing a multi-MB .xlsm binary. Capture
# a known-good artifact once with --save; every later run checks a fresh artifact against
# it with --check. Goldens live under tests/characterization/goldens/.

def save_golden(artifact: str | Path, golden_path: str | Path) -> None:
    grid = _load(artifact)
    gp = Path(golden_path)
    gp.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(gp, "wt", encoding="utf-8") as f:
        json.dump(grid, f, ensure_ascii=False)


def _load_golden(golden_path: str | Path) -> dict:
    with gzip.open(golden_path, "rt", encoding="utf-8") as f:
        return json.load(f)


def compare_to_golden(golden_path: str | Path, candidate: str | Path) -> Report:
    """Compare a fresh artifact against a stored golden grid."""
    return _compare_grids(_load_golden(golden_path), _load(candidate))


def main(argv: list[str]) -> int:
    usage = ("usage:\n"
             "  verify_goldens.py GOLDEN CANDIDATE        compare two live artifacts\n"
             "  verify_goldens.py --save ARTIFACT GOLDEN  capture a golden grid (.json.gz)\n"
             "  verify_goldens.py --check GOLDEN CANDIDATE check an artifact vs a golden")
    if len(argv) == 4 and argv[1] == "--save":
        save_golden(argv[2], argv[3])
        print(f"golden saved: {argv[3]}")
        return 0
    if len(argv) == 4 and argv[1] == "--check":
        rep = compare_to_golden(argv[2], argv[3])
        print(rep.summary())
        return 0 if rep.identical else 1
    if len(argv) == 3:
        rep = compare_artifacts(argv[1], argv[2])
        print(rep.summary())
        return 0 if rep.identical else 1
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
