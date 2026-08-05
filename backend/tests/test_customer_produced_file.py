"""The Customer CSVs Oracle actually receives — produced, opened, and read.

WHY THIS EXISTS
---------------
``test_customer_fbdi_sequence.py`` tests the layout FUNCTION and asserts that
``output_service`` calls it. Both passed on 05-Aug while EVERY Customer
conversion in production failed:

    _apply_customer_layout() got an unexpected keyword argument 'with_end'

The seam test could not have caught it. It reads the generator's source and
checks the call is written:

    check("with the END terminator", "with_end=True)" in out)

which proves the caller exists and says nothing about whether the callee accepts
what it passes. The nested wrapper in ``generate_output_artifact`` had not been
given ``with_end`` when the layout module grew it, so the call raised TypeError
before a single CSV was written.

``test_call_matches_signature.py`` closes that statically, for every call in the
backend. This closes it the way BOM is closed — by producing the file. The two
overlap on purpose: the sweep is cheap and general, this one is specific and
cannot be satisfied by anything short of a working generator.

WHAT IS CHECKED
---------------
  1. Generation returns a package at all. This is the assertion that fails
     against the broken wrapper, and the only one that matters on the day.
  2. Fifteen interfaces — the ones NextPower loads — and not the four the
     analyst excluded.
  3. Oracle's own file names, each carrying the load-sequence prefix, because
     the sequence is part of the deliverable.
  4. Column order matches ``customer_fbdi_column_order.json``'s ``csv_order``,
     which is the CSV sequence and differs from the worksheet order on three
     interfaces. Same count either way, so only position can tell them apart.
  5. Every file ends with the END record terminator. That is what ``with_end``
     exists for and what V2_2 of the sequence workbook added; the bug removed
     it from all fifteen by removing the files entirely.

THE IN-MEMORY DATABASE
----------------------
``mongomock_motor`` backs Beanie in-process, which is what makes an end-to-end
generate runnable without infrastructure. A skip here means the only test that
opens a produced Customer file is not running — treat it as red, not as noise.
"""
import csv
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the produced-file Customer check cannot run",
)

import pandas as pd                                              # noqa: E402
from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

from app.config import settings                                  # noqa: E402
from app.models.client import Client                             # noqa: E402
from app.models.conversion import Conversion                     # noqa: E402
from app.models.dataset import Dataset, DatasetColumnProfile     # noqa: E402
from app.models.fbdi import (                                    # noqa: E402
    FBDIField, FBDISheet, FBDITemplate, FBDITemplateFile, GoldStandard, OracleLookup,
)
from app.models.mapping import MappingSuggestion                 # noqa: E402
from app.models.output import ConvertedOutput                    # noqa: E402
from app.models.project import Project                           # noqa: E402
from app.models.transformation import Crosswalk, TransformationRule  # noqa: E402
from app.models.user import User                                 # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
_TEMPLATE = (_BACKEND / "app" / "data" / "fbdi_templates"
             / "CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm")
_SPEC = json.loads((_BACKEND / "app" / "data"
                    / "customer_fbdi_column_order.json").read_text(encoding="utf-8"))
_SHEETS = _SPEC["sheets"]
_SCOPE = _SPEC["load_scope"]
_SEQUENCE = _SPEC["load_sequence"]
_EXCLUDED = [e["sheet"] for e in _SPEC["excluded_interfaces"]]

# One distinctive value, so a misplaced column shows as a value at the wrong
# index rather than as a subtle difference in a name.
#
# It goes into "Organization Name" on Parties rather than into a field every
# interface shares, because the fields every interface shares are the LINKAGE
# fields — Batch Identifier, Party Original System, the source references — and
# ``customer_structure_service.apply_to_frame`` generates those. Probing one of
# them measures the glue, not the mapping. Organization Name is content: nothing
# but the analyst's mapping can put a value there.
_PROBE_COLUMN = "ORGNAME"
_PROBE_VALUE = "PROBE-ORG-05AUG"
_PROBE_SHEET = "HZ_IMP_PARTIES_T"
_PROBE_FIELD = "Organization Name"

# And a SECOND probe, on a column the glue DOES generate, recorded the way the
# learning engine records one. This is the 05-Aug live defect: the screen said
# NETSUITE, the file said LEGACY, on three linkage columns at once.
_GLUE_FIELD = "Party Original System"
_GLUE_VALUE = "PROBE-ENGINE-CONSTANT"
_ENGINE = "learning-engine"

# A THIRD probe, on an OPTIONAL in-scope interface that nothing else touches, to
# prove an engine-recorded constant switches it on. Tejaswini, 31-Jul: "Role Type
# … set … for both target sheets, however it only reflects in the
# HZ_IMP_ACCTCONTACTS_T sheet and not in HZ_IMP_CONTACTROLES."
_OPT_SHEET = "HZ_IMP_CONTACTROLES"
_OPT_FIELD = "Role Type"
_OPT_VALUE = "PROBE-ROLE"

# And a FOURTH, on an interface the analyst EXCLUDED. Load scope is the guard now
# that authorship no longer is, so a constant here must still ship nowhere.
_EXCL_SHEET = "RA_CUSTOMER_BANKS_INT_ALL"
_EXCL_VALUE = "PROBE-EXCLUDED"


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


_MODELS = [
    User, Client, Project, Conversion, Dataset, DatasetColumnProfile,
    FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
    GoldStandard, MappingSuggestion, TransformationRule, Crosswalk, ConvertedOutput,
]


async def _build_conversion(tmp: Path):
    from app.parsers import parse_fbdi_template

    parsed = parse_fbdi_template(_TEMPLATE)
    template = FBDITemplate(name="Customer Import", business_object="Customer",
                            file_name=_TEMPLATE.name, status="parsed", is_global=True)
    await template.insert()

    sheets = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(template_id=template.id, sheet_name=s["sheet_name"],
                          sequence=s.get("sequence", 0),
                          field_count=s.get("field_count", 0))
        await sheet.insert()
        sheets[s["sheet_name"]] = sheet

    fields: dict[str, list] = {}
    for f in parsed["fields"]:
        sheet = sheets[f["sheet_name"]]
        field = FBDIField(template_id=template.id, sheet_id=sheet.id,
                          field_name=f["field_name"], display_name=f.get("display_name"),
                          required=f.get("required", False), data_type=f.get("data_type"),
                          sequence=f.get("sequence", 0))
        await field.insert()
        fields.setdefault(f["sheet_name"], []).append(field)

    source = tmp / "customer_source.csv"
    pd.DataFrame({_PROBE_COLUMN: [_PROBE_VALUE, _PROBE_VALUE + "-2"]}).to_csv(
        source, index=False)

    client = Client(name="Probe", code="PRB", is_default=True); await client.insert()
    project = Project(name="Customer produced-file check", client_id=client.id)
    await project.insert()
    dataset = Dataset(name="customer source", file_name=source.name,
                      file_path=str(source), file_type="csv",
                      row_count=2, column_count=1)
    await dataset.insert()
    conversion = Conversion(project_id=project.id, name="Customer produced-file check",
                            dataset_id=dataset.id, dataset_ids=[dataset.id],
                            template_id=template.id, target_object="Customer",
                            source_type="dataset")
    await conversion.insert()

    # The spec labels columns as Oracle prints them, asterisk and all
    # ("*Batch Identifier"); the parser strips the required-marker into
    # ``display_name`` and keeps a bare ``field_name``. Match on either, so the
    # probe is looked up the way the spec names it.
    by_name = {}
    for f in fields.get(_PROBE_SHEET, []):
        by_name.setdefault(f.field_name, f)
        if f.display_name:
            by_name.setdefault(f.display_name, f)
    field = by_name.get(_PROBE_FIELD) or by_name.get(_PROBE_FIELD.lstrip("*"))
    check(f"{_PROBE_SHEET} has {_PROBE_FIELD}", field is not None)
    await MappingSuggestion(conversion_id=conversion.id,
                            target_field_id=field.id,
                            source_column=_PROBE_COLUMN,
                            confidence=1.0, status="approved").insert()

    gfield = by_name.get(_GLUE_FIELD)
    check(f"{_PROBE_SHEET} has {_GLUE_FIELD}", gfield is not None)
    await MappingSuggestion(conversion_id=conversion.id,
                            target_field_id=gfield.id,
                            default_value=_GLUE_VALUE,
                            confidence=1.0, status="approved",
                            approved_by=_ENGINE).insert()

    async def _engine_const(sheet_name: str, field_name: str, value: str):
        for f in fields.get(sheet_name, []):
            if f.field_name == field_name or (f.display_name or "") == field_name:
                await MappingSuggestion(conversion_id=conversion.id,
                                        target_field_id=f.id, default_value=value,
                                        confidence=1.0, status="approved",
                                        approved_by=_ENGINE).insert()
                return True
        return False

    check(f"{_OPT_SHEET} has {_OPT_FIELD}",
          await _engine_const(_OPT_SHEET, _OPT_FIELD, _OPT_VALUE))
    # Any field will do on the excluded tab — the point is that scope, not the
    # field, is what keeps it out.
    _excl = fields.get(_EXCL_SHEET) or []
    check(f"{_EXCL_SHEET} is in the template", bool(_excl))
    await MappingSuggestion(conversion_id=conversion.id,
                            target_field_id=_excl[-1].id, default_value=_EXCL_VALUE,
                            confidence=1.0, status="approved",
                            approved_by=_ENGINE).insert()
    return conversion


_GENERATED: dict = {}


def _generated() -> dict:
    """Generate once: ``{"with_header": {file: rows}, "headerless": {...}}``."""
    if _GENERATED:
        return _GENERATED
    import asyncio

    async def run():
        tmp = Path(tempfile.mkdtemp(prefix="customer_produced_"))
        settings.UPLOAD_DIR = str(tmp)
        settings.OUTPUT_DIR = str(tmp / "outputs")
        await init_beanie(database=AsyncMongoMockClient()["customer_produced"],
                          document_models=_MODELS)
        conversion = await _build_conversion(tmp)
        from app.services.output_service import generate_output_artifact

        out = {}
        for key, header in (("with_header", True), ("headerless", False)):
            artifact = await generate_output_artifact(conversion, fmt="csv",
                                                      include_header=header)
            path = Path(artifact.output_file_path)
            check(f"{key} is a zip package", path.suffix == ".zip", f"got {path.name}")
            with zipfile.ZipFile(path) as zf:
                out[key] = {
                    name: list(csv.reader(io.StringIO(
                        zf.read(name).decode("utf-8-sig"))))
                    for name in zf.namelist()
                }
        return out

    _GENERATED.update(asyncio.run(run()))
    return _GENERATED


def _iface_of(file_name: str) -> str:
    """``03_HzImpPartySiteUsesT.csv`` -> ``HZ_IMP_PARTYSITEUSES_T``."""
    base = file_name.split("_", 1)[-1].rsplit(".", 1)[0]
    for iface, spec in _SHEETS.items():
        if spec["csv"] == base:
            return iface
    raise AssertionError(f"produced a file no interface claims: {file_name}")


# ---------------------------------------------------------------------------
# The one that fails against the broken wrapper
# ---------------------------------------------------------------------------
def test_generation_actually_returns_a_customer_package():
    """05-Aug: this raised TypeError inside the zip loop, so no artifact was ever
    written and every Customer conversion was marked failed. Stated first and on
    its own, because everything below it is moot if this cannot run."""
    for key in ("with_header", "headerless"):
        files = _generated()[key]
        check(f"{key} produced files", bool(files))
    # Rows are asserted on the sheet the probe is mapped into rather than on all
    # fifteen. An interface nothing is mapped into ships headers-only on purpose
    # — the Gap-1 rule, after a naive fan-out wrote 5,599 rows of pure linkage
    # glue into optional entity tables — so "every file has rows" would be
    # asserting the opposite of the intended behaviour.
    name = next(n for n in _generated()["headerless"] if _iface_of(n) == _PROBE_SHEET)
    check(f"{name} carries data rows", len(_generated()["headerless"][name]) >= 1)


# ---------------------------------------------------------------------------
# Scope, names and sequence
# ---------------------------------------------------------------------------
def test_the_package_holds_the_fifteen_loaded_interfaces_and_not_the_four_excluded():
    names = sorted(_generated()["headerless"])
    check("fifteen files", len(names) == len(_SCOPE), f"got {len(names)}: {names}")
    got = {_iface_of(n) for n in names}
    check("exactly the load scope", got == set(_SCOPE),
          f"missing {sorted(set(_SCOPE) - got)}, extra {sorted(got - set(_SCOPE))}")
    for excluded in _EXCLUDED:
        check(f"{excluded} is not shipped", excluded not in got)


def test_the_files_carry_oracles_names_in_the_load_sequence():
    """An analyst unzipping into a folder gets them alphabetically unless the
    sequence is in the name, and the load ORDER is part of the deliverable."""
    for name in _generated()["headerless"]:
        prefix, _, rest = name.partition("_")
        check(f"{name} is numbered", prefix.isdigit() and len(prefix) == 2,
              f"prefix {prefix!r}")
        iface = _iface_of(name)
        want = _SEQUENCE.index(iface) + 1
        check(f"{name} is number {want:02d} in the load sequence",
              int(prefix) == want, f"got {prefix}")
        check(f"{name} uses Oracle's file name",
              rest == f"{_SHEETS[iface]['csv']}.csv", f"got {rest!r}")


# ---------------------------------------------------------------------------
# The column order — the silent corruption, and what the spec exists to prevent
# ---------------------------------------------------------------------------
def test_every_produced_column_is_the_csv_order_column_in_the_csv_order_position():
    """``csv_order`` and ``fbdi_order`` differ on three of the fifteen and hold
    the same COUNT, so a file in the worksheet order is indistinguishable by eye
    and loads every value one column across."""
    for name, rows in _generated()["with_header"].items():
        iface = _iface_of(name)
        want = [str(c).strip() for c in _SHEETS[iface]["csv_order"]]
        got = [c.strip() for c in rows[0]]
        got_no_end = [c for c in got if c.upper() != "END"]
        check(f"{name} width", len(got_no_end) == len(want),
              f"produced {len(got_no_end)} vs spec {len(want)}")
        for i, (a, b) in enumerate(zip(got_no_end, want), 1):
            check(f"{name} col {i}", a == b, f"produced={a!r} spec={b!r}")


def test_a_mapped_value_lands_at_the_index_the_spec_gives_it():
    """The check that cannot pass by accident. Reads the file the way the loader
    reads it — by position — and asks whether the value the analyst mapped into
    "Organization Name" is sitting where the spec says that column is."""
    name = next(n for n in _generated()["headerless"]
                if _iface_of(n) == _PROBE_SHEET)
    rows = _generated()["headerless"][name]
    order = [str(c).strip() for c in _SHEETS[_PROBE_SHEET]["csv_order"]]
    want_index = order.index(_PROBE_FIELD)
    at = [(r, i) for r, row in enumerate(rows)
          for i, v in enumerate(row) if v.strip() == _PROBE_VALUE]
    check(f"{name}: the probe reaches the shipped file", bool(at),
          "the mapped value is not in the CSV at all")
    for r, i in at:
        check(f"{name} row {r}: {_PROBE_FIELD} is at position {want_index}",
              i == want_index,
              f"landed at {i} = {order[i] if i < len(order) else '?'!r}")


def test_a_constant_the_engine_recorded_is_not_overwritten_by_the_linkage_glue():
    """The 05-Aug live defect, on the file rather than on the rule.

    ``customer_structure_service.apply_to_frame`` generates the linkage columns —
    Batch Identifier, Party Original System, the source-system references. It is a
    FALLBACK for what no source supplies, and it skips any column the analyst has
    decided. "Decided" was resolved through ``analyst_default``, which returned
    None for anything approved by the learning engine, so an engine-recorded
    constant was not protected and the glue wrote over it.

    The shipped Customer file said LEGACY where the mapping screen said NETSUITE,
    on three columns at once, with no error anywhere.

    This maps a constant onto ``Party Original System`` with
    ``approved_by="learning-engine"`` — the exact provenance of the live rows —
    and reads the produced CSV. Before the fix the cell holds the glue's source
    system; after it, the analyst's value."""
    name = next(n for n in _generated()["headerless"]
                if _iface_of(n) == _PROBE_SHEET)
    rows = _generated()["headerless"][name]
    order = [str(c).strip() for c in _SHEETS[_PROBE_SHEET]["csv_order"]]
    i = order.index(_GLUE_FIELD)
    check(f"{name} has data rows", bool(rows))
    for r, row in enumerate(rows):
        got = row[i].strip() if i < len(row) else "(missing)"
        check(f"{name} row {r}: {_GLUE_FIELD} is the recorded constant",
              got == _GLUE_VALUE,
              f"got {got!r} — the glue overwrote a decided column")


def test_an_engine_constant_switches_an_optional_interface_on():
    """Tejaswini's 31-Jul row 31, closed against a produced file.

    An optional child interface emits rows when something is decided for it. That
    used to require a PERSON, which in practice meant "typed into this grid" —
    every constant arriving from the library carries `approved_by=learning-engine`.
    So `Role Type = CONTACT` set for both target sheets reached
    HZ_IMP_ACCTCONTACTS_T (which has mapped columns anyway) and never reached
    HZ_IMP_CONTACTROLES, which is precisely what was reported."""
    name = next(n for n in _generated()["headerless"] if _iface_of(n) == _OPT_SHEET)
    rows = _generated()["headerless"][name]
    check(f"{name} carries rows", bool(rows),
          "an optional interface with a decided constant shipped headers-only")
    order = [str(c).strip() for c in _SHEETS[_OPT_SHEET]["csv_order"]]
    i = next((k for k, c in enumerate(order) if c.strip().lstrip("*") == _OPT_FIELD), None)
    check(f"{_OPT_FIELD} is in the spec for {_OPT_SHEET}", i is not None)
    got = rows[0][i].strip() if i < len(rows[0]) else "(missing)"
    check(f"{name}: {_OPT_FIELD} carries the constant", got == _OPT_VALUE,
          f"got {got!r}")


def test_load_scope_still_holds_an_excluded_interface_out():
    """The other guard, now that authorship is not one.

    A constant on RA_CUSTOMER_BANKS_INT_ALL must ship nowhere: the analyst named
    the fifteen interfaces this client loads and that one is not among them. If
    widening the constant rule ever reopens an excluded tab, this is the test that
    says so."""
    names = set(_generated()["headerless"])
    for n in names:
        check(f"{n} is not the excluded tab", _iface_of(n) != _EXCL_SHEET)
    for n, rows in _generated()["headerless"].items():
        for r, row in enumerate(rows):
            check(f"{n} row {r} does not carry the excluded constant",
                  _EXCL_VALUE not in [c.strip() for c in row])


# ---------------------------------------------------------------------------
# END — the terminator the broken keyword was carrying
# ---------------------------------------------------------------------------
def test_every_customer_csv_ends_with_the_record_terminator():
    """V2_2 spells it out: all fifteen CSV columns now end with an explicit END.
    The supplier package has always written it; this one never did until the
    ``with_end`` keyword was added — the same keyword the wrapper then dropped.

    Asserted on the files that carry data. An interface nothing is mapped into
    ships headers-only by design, and a terminator with no records to terminate
    is not what V2_2 asked for."""
    checked = 0
    for name, rows in _generated()["headerless"].items():
        if not rows:
            continue
        flat = [c.strip().upper() for row in rows for c in row]
        check(f"{name} carries an END", "END" in flat,
              "no terminator anywhere in the file")
        checked += 1
    check("at least one file was actually checked", checked >= 1,
          "every interface came back empty — the terminator check proved nothing")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall produced-file Customer checks passed")
