"""EffectiveStartDate mapped to Hire Date must reach Worker/Assignment — and a
constant on Location/Job must NOT follow it there.

THE DEFECT (06-Aug, NextPower Employee HDL)
-------------------------------------------
EffectiveStartDate is one attribute NAME that lives on nine HDL components:
Location and Job carry it as a fixed setup date; every Worker-family component
(Worker, PersonName, WorkTerms, Assignment, AssignmentSupervisor) carries it as a
DATE mapped to Hire Date. The analyst set the fixed value 1900/01/01 on
Location/Job's EffectiveStartDate.

The HDL writer keyed the analyst's fixed values by field NAME alone
(``const_override["EffectiveStartDate"]``). That one entry is shared across every
component, so the Location/Job constant answered for the Worker cells too — and the
generated Worker.dat / Assignment shipped 1900/01/01 where the client expected the
employee's hire date. Reported: "the Hire Date value is not being reflected ... for
both Worker and Worker Assignment. For Location and Job ... 1900/01/01 ... is
correct."

THE FIX
-------
Every analyst statement — fixed value, keep-blank, source column, transformation
rule — is keyed by (component, field), the component taken from the field's own
seeded sheet. A constant on Location can no longer be looked up under Worker, so
Worker/Assignment fall through to their schema spec (``_date(EffectiveStartDate,
Hire Date)``) and resolve the hire date. Location keeps 1900/01/01.

This pins the fix at three levels: the pure ``render_cell`` contract, the source
shape of the keying, and — the one that actually reproduces the bug — a real
generation run whose Worker.dat is read back and checked value-by-value.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hdl_output_service import NO_ANSWER, render_cell   # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _src() -> str:
    return (_BACKEND / "app" / "services"
            / "hdl_output_service.py").read_text(encoding="utf-8")


# ── 1. The render_cell contract: a per-component analyst answer stays local ───

def test_a_constant_on_one_component_is_invisible_to_another():
    """render_cell already takes a per-field analyst callable; the fix builds that
    callable PER COMPONENT. Simulate the exact collision: the SAME field name on two
    components, an analyst answer on only one of them."""
    # Location: analyst set 1900/01/01 here (its schema default is the 1990 const).
    loc_spec = {"kind": "const", "name": "EffectiveStartDate", "value": "1990/01/01"}
    loc_answer = lambda fn: "1900/01/01" if fn == "EffectiveStartDate" else NO_ANSWER
    check("Location writes the analyst's fixed value",
          render_cell(loc_spec, lambda _f, _s: None, loc_answer) == "1900/01/01")

    # Worker: same field name, analyst said NOTHING here — the constant must not
    # follow. Its schema spec is a date resolved from Hire Date.
    wkr_spec = {"kind": "date", "name": "EffectiveStartDate", "source": "Hire Date"}
    wkr_answer = lambda _fn: NO_ANSWER                     # per-component: silent here
    got = render_cell(wkr_spec, lambda _f, _s: "2017/06/01", wkr_answer)
    check("Worker resolves the hire date, not the leaked constant",
          got == "2017/06/01", f"got {got}")


# ── 2. The source shape: every statement is keyed by (component, field) ───────

def test_the_analyst_statements_are_keyed_by_component_not_field_name():
    src = _src()
    check("fixed values keyed by (component, field)",
          "const_override[(comp, fn)] = _dv" in src)
    check("keep-blank keyed by (component, field)",
          'const_override[(comp, fn)] = ""' in src)
    check("source columns keyed by (component, field)",
          "override[(comp, fn)] = m.source_column" in src)
    check("approval dates keyed by (component, field)",
          "const_at[(comp, fn)] = _dv_at" in src)
    check("rules keyed by (component, field)",
          "rules_by_field.setdefault((_rcomp, _rfn)" in src)
    check("the component comes from the field's own sheet",
          "fcomp_by_id" in src and "_sheet_name_by_id" in src)


def test_the_generation_loop_tells_each_cell_its_component():
    """The seam. If _cell is never told which component it is writing, the
    per-component keys cannot be looked up and the fix is inert."""
    src = _src()
    check("the cell is handed its component", "_cell(r, f, _cn)" in src)
    check("normalised the same way the analyst-side keys were",
          "_cn = _norm(comp_name)" in src)
    check("the analyst lookup is component-aware",
          "def _analyst(comp: str, field_name: str)" in src)
    check("the column resolver is component-aware",
          "def _resolve_col(comp: str, field_name: str" in src)


def test_the_field_wide_fallback_only_covers_an_unresolvable_component():
    """The fallback exists so a mapping whose component cannot be resolved still
    behaves as before — but it must be populated ONLY then, or it would re-leak the
    very constant this change isolates."""
    src = _src()
    i = src.index("const_override[(comp, fn)] = _dv")
    block = src[i:i + 700]
    check("the field-wide fallback is guarded by 'no component'",
          "if not comp:" in block and "const_override_any[fn] = _dv" in block,
          block[:400])


# ── 3. The real generation run: Worker.dat is read back and checked ───────────

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the end-to-end HDL run cannot execute",
)


def _dat_texts(zip_bytes: bytes) -> list[str]:
    """Every .dat text inside the nested HDL bundle (outer zip → per-object zip →
    .dat)."""
    import io
    import zipfile
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as outer:
        for name in outer.namelist():
            with zipfile.ZipFile(io.BytesIO(outer.read(name))) as inner:
                for dn in inner.namelist():
                    out.append(inner.read(dn).decode("utf-8"))
    return out


def _first_value(texts: list[str], component: str, field: str):
    """The value of ``field`` on the first MERGE record of ``component``, wherever
    that component block appears across the bundle."""
    for text in texts:
        cols = None
        for ln in text.splitlines():
            parts = ln.split("|")
            if len(parts) < 2 or parts[1] != component:
                continue
            if parts[0] == "METADATA":
                cols = parts[2:]
            elif parts[0] == "MERGE" and cols is not None and field in cols:
                return parts[2:][cols.index(field)]
    return None


def test_the_generated_worker_dat_carries_the_hire_date_not_the_setup_constant():
    """The reproduction. Seed the Employee HDL template, a one-row dataset, and the
    analyst's 1900/01/01 fixed value on Location.EffectiveStartDate — then GENERATE
    and read the .dat files back."""
    import asyncio
    import tempfile

    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.models.client import Client
    from app.models.conversion import Conversion
    from app.models.dataset import Dataset, DatasetColumnProfile
    from app.models.fbdi import (
        FBDIField, FBDISheet, FBDITemplate, FBDITemplateFile, GoldStandard, OracleLookup,
    )
    from app.models.learned import ArchivedMappingDecision, LearnedMapping
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput
    from app.models.project import Project
    from app.models.transformation import Crosswalk, TransformationRule
    from app.models.user import User

    models = [
        User, Client, Project, Conversion, Dataset, DatasetColumnProfile,
        FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
        GoldStandard, MappingSuggestion, TransformationRule, Crosswalk,
        ConvertedOutput, LearnedMapping, ArchivedMappingDecision,
    ]

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["hdl_esd_scope"],
                          document_models=models)
        from app.services.hdl_output_service import generate_hdl_artifact

        # A one-row Workday extract on disk (materialize_dataset_file prefers the
        # on-disk copy, so no GridFS is needed).
        tmp = Path(tempfile.mkdtemp())
        csv = tmp / "extract.csv"
        csv.write_text(
            "Employee ID,Hire Date,Location,Location Code,Active Status,Country\n"
            "1001,2017/06/01,HQ,NX_HQ,Active,United States\n",
            encoding="utf-8")

        tpl = FBDITemplate(name="Employee HDL Import", business_object="Employee HDL",
                           file_name="none", status="parsed", is_global=True)
        await tpl.insert()

        # Two component sheets, named EXACTLY as the schema's components so the
        # mapping→component resolution lines up. Only EffectiveStartDate matters.
        loc_sheet = FBDISheet(template_id=tpl.id, sheet_name="Location",
                              sequence=0, field_count=1)
        await loc_sheet.insert()
        loc_esd = FBDIField(template_id=tpl.id, sheet_id=loc_sheet.id,
                            field_name="EffectiveStartDate",
                            display_name="EffectiveStartDate *", sequence=1)
        await loc_esd.insert()

        wkr_sheet = FBDISheet(template_id=tpl.id, sheet_name="Worker",
                              sequence=1, field_count=1)
        await wkr_sheet.insert()
        wkr_esd = FBDIField(template_id=tpl.id, sheet_id=wkr_sheet.id,
                            field_name="EffectiveStartDate",
                            display_name="EffectiveStartDate *", sequence=2)
        await wkr_esd.insert()

        client = Client(name="NextPower", code="NXT", is_default=True)
        await client.insert()
        project = Project(name="Employee", client_id=client.id)
        await project.insert()
        ds = Dataset(name="ext", file_name="extract.csv", file_path=str(csv),
                     file_type="csv", row_count=1, column_count=6)
        await ds.insert()
        conv = Conversion(project_id=project.id, name="Employee HDL",
                          template_id=tpl.id, dataset_id=ds.id,
                          target_object="Employee HDL", source_type="dataset")
        await conv.insert()

        # THE analyst statements: 1900/01/01 fixed on Location.EffectiveStartDate,
        # and Worker.EffectiveStartDate pointed at the Hire Date column.
        await MappingSuggestion(
            conversion_id=conv.id, target_field_id=loc_esd.id,
            default_value="1900/01/01", confidence=1.0, status="approved",
        ).insert()
        await MappingSuggestion(
            conversion_id=conv.id, target_field_id=wkr_esd.id,
            source_column="Hire Date", confidence=1.0, status="approved",
        ).insert()

        art = await generate_hdl_artifact(conv, fmt="dat")
        texts = _dat_texts(Path(art.output_file_path).read_bytes())
        try:
            return {
                "location": _first_value(texts, "Location", "EffectiveStartDate"),
                "worker": _first_value(texts, "Worker", "EffectiveStartDate"),
                "assignment": _first_value(texts, "Assignment", "EffectiveStartDate"),
            }
        finally:
            # Don't leave the generated bundle behind.
            try:
                Path(art.output_file_path).unlink()
                Path(art.output_file_path).parent.rmdir()
            except OSError:
                pass

    got = asyncio.run(run())

    check("Worker.EffectiveStartDate is the hire date, not the setup constant",
          got["worker"] == "2017/06/01", f"got {got['worker']!r}")
    check("Assignment (\"Worker Assignment\") is the hire date too",
          got["assignment"] == "2017/06/01", f"got {got['assignment']!r}")
    check("Location keeps the analyst's 1900/01/01",
          got["location"] == "1900/01/01", f"got {got['location']!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nEffectiveStartDate stays per-component: Worker gets Hire Date, "
          "Location keeps 1900/01/01")
