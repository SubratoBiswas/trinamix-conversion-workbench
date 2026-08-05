"""One field, two interfaces, two different answers — the fourth key dimension.

THE REQUIREMENT
---------------
Analyst, 05-Aug: "client, source, field, sheet should be 4 dimensions for all
columns, for all source and fields ... I want one sheet to be updated to I but
others to be blank, this is not happening."

Insert Update Indicator is the case. A gold reference put a field-wide default "I"
in the store; it reaches every one of the 19 Customer interfaces. The analyst
presses Keep blank on ONE interface and expects THAT interface blank and the rest
still "I". It could not happen, because the store keyed (client, source, field)
and one field held one value — a per-sheet keep-blank and the field-wide default
collapsed into a single row.

THE FIX, MEASURED HERE
----------------------
The store key gained the sheet. A field-wide default (sheets=[]) and a
sheet-scoped keep-blank (sheets=[X]) now coexist as two rows. `resolve(field,
sheet=X)` returns the keep-blank on X (newer, applies to X) and the field-wide
default everywhere else. `apply_learned_to_conversion` resolves per sheet, so it
writes the per-interface answer onto each row.

This drives that whole chain against mongomock: seed the two store rows, run the
apply pass, read the resulting per-sheet mapping rows.
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the per-sheet decision check cannot run",
)

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

from app.models.client import Client                             # noqa: E402
from app.models.conversion import Conversion                     # noqa: E402
from app.models.dataset import Dataset, DatasetColumnProfile     # noqa: E402
from app.models.fbdi import (                                    # noqa: E402
    FBDIField, FBDISheet, FBDITemplate, FBDITemplateFile, GoldStandard, OracleLookup,
)
from app.models.learned import ArchivedMappingDecision, LearnedMapping  # noqa: E402
from app.models.mapping import MappingSuggestion                 # noqa: E402
from app.models.output import ConvertedOutput                    # noqa: E402
from app.models.project import Project                           # noqa: E402
from app.models.transformation import Crosswalk, TransformationRule  # noqa: E402
from app.models.user import User                                 # noqa: E402

_MODELS = [
    User, Client, Project, Conversion, Dataset, DatasetColumnProfile,
    FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
    GoldStandard, MappingSuggestion, TransformationRule, Crosswalk, ConvertedOutput,
    LearnedMapping, ArchivedMappingDecision,
]

_FIELD = "Insert Update Indicator"
_BLANK_SHEET = "RA_CUSTOMER_PROFILES_INT_ALL"
_KEEP_SHEETS = ("HZ_IMP_PARTIES_T", "HZ_IMP_ACCOUNTS_T")


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


async def _seed_conversion():
    template = FBDITemplate(name="Customer Import", business_object="Customer",
                            file_name="t.xlsm", status="parsed", is_global=True)
    await template.insert()
    field_by_sheet = {}
    for name in (_BLANK_SHEET, *_KEEP_SHEETS):
        sh = FBDISheet(template_id=template.id, sheet_name=name, sequence=1,
                       field_count=1)
        await sh.insert()
        f = FBDIField(template_id=template.id, sheet_id=sh.id, field_name=_FIELD,
                      display_name=f"*{_FIELD}", sequence=1)
        await f.insert()
        field_by_sheet[name] = f

    client = Client(name="NextPower", code="NP", is_default=True); await client.insert()
    project = Project(name="P", client_id=client.id); await project.insert()
    conv = Conversion(project_id=project.id, name="Customer 03082026",
                      template_id=template.id, target_object="Customer",
                      source_type="dataset")
    await conv.insert()
    rows = {}
    for name, f in field_by_sheet.items():
        m = MappingSuggestion(conversion_id=conv.id, target_field_id=f.id,
                              source_column=None, confidence=0.0, status="suggested")
        await m.insert()
        rows[name] = m
    return conv, rows


def test_a_field_is_I_on_some_sheets_and_blank_on_one():
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["per_sheet"],
                          document_models=_MODELS)
        from app.services import mapping_store
        from app.services.learning_service import apply_learned_to_conversion

        conv, rows = await _seed_conversion()

        # 1) The gold reference: Insert Update Indicator = "I", FIELD-WIDE.
        yesterday = datetime.utcnow() - timedelta(days=1)
        await mapping_store.record_learning(
            kind="example_default", category="Default Value",
            original_value="(default)", resolved_value="I",
            target_object="Customer", target_field=_FIELD,
            client_id=conv.project_id and None,  # global default, like a gold import
            captured_from="CustomerExpand gold", captured_by="gold",
            effective_date=yesterday)

        # 2) The analyst keeps blank on ONE interface — scoped to that sheet, today.
        await mapping_store.record_learning(
            kind="suppress_field", category="Left blank on purpose",
            original_value="(blank)", resolved_value="",
            target_object="Customer", target_field=_FIELD,
            captured_from="kept blank in Mapping Review", captured_by="analyst",
            sheets=[_BLANK_SHEET], revive=True)

        # Two rows, not one: the field-wide default and the sheet-scoped blank.
        stored = await mapping_store.find_rows_for_key(target_field=_FIELD)
        live = [r for r in stored if not getattr(r, "is_deleted", False)]
        check("the store keeps them apart — two rows, one per key", len(live) == 2,
              f"got {len(live)}: {[(r.kind, r.sheets) for r in live]}")

        # 3) The apply pass resolves per sheet and writes the rows.
        await apply_learned_to_conversion(conv,
                                          [rows[n] for n in rows])

        refreshed = {n: await MappingSuggestion.get(rows[n].id) for n in rows}
        blanked = refreshed[_BLANK_SHEET]
        check(f"{_BLANK_SHEET} is kept blank", blanked.status == "not_applicable",
              f"got {blanked.status} dv={blanked.default_value!r}")
        for n in _KEEP_SHEETS:
            r = refreshed[n]
            check(f"{n} still carries I", r.default_value == "I",
                  f"got status={r.status} dv={r.default_value!r}")
        return True

    check("end to end", asyncio.run(run()))


def test_setting_a_fixed_value_supersedes_a_keep_blank_on_the_same_sheet():
    """"I am not able to remove kept blank and add a fixed value."

    On one interface: keep blank first, then set a fixed value. Both are decisions
    about (client, source, field, THAT sheet), so the later one — the fixed value —
    replaces the keep-blank. Before the fourth dimension the fixed value on one
    sheet and the keep-blank on another were the same key and fought; now the
    override is only against the same interface, and it wins by date."""
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["override"],
                          document_models=_MODELS)
        from app.services import mapping_store

        t0 = datetime.utcnow() - timedelta(minutes=5)
        # Keep blank on the sheet, earlier.
        await mapping_store.record_learning(
            kind="suppress_field", category="Left blank on purpose",
            original_value="(blank)", resolved_value="",
            target_object="Customer", target_field=_FIELD,
            captured_from="kept blank", captured_by="analyst",
            sheets=[_BLANK_SHEET], effective_date=t0, revive=True)
        # Then a fixed value on the SAME sheet, later.
        await mapping_store.record_learning(
            kind="example_default", category="Default Value",
            original_value="(default)", resolved_value="I",
            target_object="Customer", target_field=_FIELD,
            captured_from="fixed value", captured_by="analyst",
            sheets=[_BLANK_SHEET], effective_date=datetime.utcnow())

        rows = await mapping_store.find_rows_for_key(target_field=_FIELD)
        live = [r for r in rows if not getattr(r, "is_deleted", False)]
        check("one row for the sheet, not a blank fighting a value", len(live) == 1,
              f"got {[(r.kind, r.resolved_value, r.sheets) for r in live]}")
        winner = mapping_store.resolve(live, target_field=_FIELD, sheet=_BLANK_SHEET)
        check("the fixed value won", winner is not None and winner.value == "I",
              f"got {winner and (winner.decision, winner.value)}")
        return True

    check("end to end", asyncio.run(run()))


if __name__ == "__main__":
    test_a_field_is_I_on_some_sheets_and_blank_on_one()
    test_setting_a_fixed_value_supersedes_a_keep_blank_on_the_same_sheet()
    print("\nper-sheet decisions hold")
