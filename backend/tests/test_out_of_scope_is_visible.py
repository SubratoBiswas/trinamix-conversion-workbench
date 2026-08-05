"""A mapping on an interface the client does not load, said out loud on the screen.

WHAT WAS WRONG
--------------
Oracle's Customer template ships 19 interface tables. NextPower loads 15 —
Tejaswini, 31-Jul: "they are working on 15 files only, mentioned in the sheet, so
we do not have to generate all of the 19 FBDI output files." The generator honours
that, and should.

What nothing honoured was the analyst's TIME. A constant set on one of the other
four is accepted by the grid, saved, shown as approved, counted in the "N decided"
figures — and ships nowhere. Two were found in exactly that state on 05-Aug while
auditing the produced files against the screen:

    Receipt Method = EMAIL           -> RA_CUST_PAY_METHOD_INT_ALL   excluded
    Bank Account Country Code = US   -> RA_CUSTOMER_BANKS_INT_ALL    excluded

Both approved. Both absent from all fifteen shipped CSVs. Nothing anywhere said
so, and there is no error to notice: an excluded interface is not generated, so
there is no file in which the value could be seen to be missing.

WHAT THIS COVERS
----------------
  * `enrich_mapping_with_samples` resolves each mapping to its INTERFACE and marks
    whether the client loads it.
  * `MappingOut` declares both fields. This is the load-bearing half: FastAPI's
    `response_model` strips whatever the schema does not name, which is how
    `mapping_sync` was written by the backend and never seen by the screen. A flag
    the grid never receives cannot warn anyone.
  * Non-Customer objects are unaffected — Supplier has no load scope, and asking a
    Customer spec about a Supplier tab would be answering a question it was not
    given.
  * The grid actually renders it.
"""
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the scope-flag check cannot run",
)

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

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
from app.schemas.mapping import MappingOut                       # noqa: E402
from app.services.mapping_service import enrich_mapping_with_samples  # noqa: E402
from app.services.supplier_fbdi_layout import customer_in_load_scope  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_FRONTEND = _ROOT.parent / "frontend" / "src"

_IN_SCOPE = "HZ_IMP_PARTIES_T"
_EXCLUDED = "RA_CUSTOMER_BANKS_INT_ALL"

_MODELS = [
    User, Client, Project, Conversion, Dataset, DatasetColumnProfile,
    FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile, OracleLookup,
    GoldStandard, MappingSuggestion, TransformationRule, Crosswalk, ConvertedOutput,
]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_the_layout_agrees_about_which_interfaces_are_loaded():
    """Cheap, no database. If this is wrong the rest measures nothing."""
    check(f"{_IN_SCOPE} is loaded", customer_in_load_scope(_IN_SCOPE))
    check(f"{_EXCLUDED} is not", not customer_in_load_scope(_EXCLUDED))


def _enriched(target_object: str) -> list[dict]:
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["scope_flag"],
                          document_models=_MODELS)
        template = FBDITemplate(name="T", business_object=target_object,
                                file_name="t.xlsm", status="parsed", is_global=True)
        await template.insert()
        out_fields = []
        for sheet_name in (_IN_SCOPE, _EXCLUDED):
            sheet = FBDISheet(template_id=template.id, sheet_name=sheet_name,
                              sequence=1, field_count=1)
            await sheet.insert()
            f = FBDIField(template_id=template.id, sheet_id=sheet.id,
                          field_name=f"F_{sheet_name}", sequence=1)
            await f.insert()
            out_fields.append((sheet_name, f))

        client = Client(name="C", code="C", is_default=True); await client.insert()
        project = Project(name="P", client_id=client.id); await project.insert()
        conv = Conversion(project_id=project.id, name="c", template_id=template.id,
                          target_object=target_object, source_type="dataset")
        await conv.insert()
        rows = []
        for _sheet, f in out_fields:
            m = MappingSuggestion(conversion_id=conv.id, target_field_id=f.id,
                                  default_value="X", confidence=1.0,
                                  status="approved", approved_by="learning-engine")
            await m.insert()
            rows.append(m)
        return await enrich_mapping_with_samples(conv, rows)

    return asyncio.run(run())


def test_a_customer_mapping_says_which_interface_it_is_on_and_whether_it_loads():
    by_sheet = {r["target_sheet"]: r for r in _enriched("Customer")}
    check("both interfaces are named", set(by_sheet) == {_IN_SCOPE, _EXCLUDED},
          f"got {sorted(str(k) for k in by_sheet)}")
    check(f"{_IN_SCOPE} is in load scope",
          by_sheet[_IN_SCOPE]["target_in_load_scope"] is True)
    check(f"{_EXCLUDED} is flagged as not loaded",
          by_sheet[_EXCLUDED]["target_in_load_scope"] is False,
          "an analyst can still set a constant here with nothing to warn them")


def test_a_supplier_mapping_is_never_flagged():
    """Supplier has no load scope. Asking the Customer spec about a Supplier tab
    would be answering a question it was not given, and its unknown-sheet fallback
    happens to be kind — which is not the same as being right."""
    for r in _enriched("Supplier"):
        check(f"{r['target_sheet']} is not flagged",
              r["target_in_load_scope"] is True)


def test_the_response_model_actually_carries_both_fields():
    """The half that decides whether any of this reaches a human.

    `response_model=list[MappingOut]` strips every key the schema does not declare.
    `mapping_sync` was written by the backend, dropped by exactly this, and spent a
    week looking like a backend bug."""
    names = set(MappingOut.model_fields)
    check("target_sheet is declared", "target_sheet" in names)
    check("target_in_load_scope is declared", "target_in_load_scope" in names)
    check("target_generated is declared", "target_generated" in names)
    check("and it defaults to in-scope, so a silent None never reads as a warning",
          MappingOut.model_fields["target_in_load_scope"].default is True)


def test_a_generated_column_is_reported_as_generated_not_as_a_gap():
    """Batch Identifier, 05-Aug, the screenshot.

        screen:  SOURCE (none) · REQUIRED · Unmapped
                 "Required field with no source and no default."
        file:    CONV-E3F9D5, on all 9,809 rows.

    Both were accurate about their own half and the screen's half was FALSE about
    the outcome. Customer's interface tables are stitched by columns no flat
    extract contains, so the tool generates them — correctly. Nothing said so."""
    from app.services.customer_structure_service import generated_role

    check("Batch Identifier is generated",
          generated_role("*Batch Identifier") is not None)
    check("so is the party linkage",
          generated_role("Party Original System Reference") is not None)
    check("a content column is not",
          generated_role("Organization Name") is None)
    check("nor is a constant the analyst sets",
          generated_role("Role Type") is None)
    check("the reference is described as the reference, not the system",
          "reference" in (generated_role("Party Original System Reference") or ""))


def test_a_decided_column_is_never_badged_as_generated():
    """The glue skips any column the analyst decided, so badging a decided column
    would describe the opposite of what happens. Checked through the enrichment,
    not the helper, because that is where the three conditions live."""
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["gen_flag"],
                          document_models=_MODELS)
        template = FBDITemplate(name="T", business_object="Customer",
                                file_name="t.xlsm", status="parsed", is_global=True)
        await template.insert()
        sheet = FBDISheet(template_id=template.id, sheet_name=_IN_SCOPE,
                          sequence=1, field_count=4)
        await sheet.insert()
        made = []
        for i in range(4):
            f = FBDIField(template_id=template.id, sheet_id=sheet.id,
                          field_name="Batch Identifier", display_name="*Batch Identifier",
                          sequence=i + 1)
            await f.insert()
            made.append(f)

        client = Client(name="C", code="C", is_default=True); await client.insert()
        project = Project(name="P", client_id=client.id); await project.insert()
        conv = Conversion(project_id=project.id, name="c", template_id=template.id,
                          target_object="Customer", source_type="dataset")
        await conv.insert()

        cases = [
            ("undecided", dict(status="approved")),
            ("a source column", dict(status="approved", source_column="BATCH")),
            ("a fixed value", dict(status="approved", default_value="B-1")),
            ("Keep blank", dict(status="not_applicable")),
        ]
        rows = []
        for f, (_label, kw) in zip(made, cases):
            m = MappingSuggestion(conversion_id=conv.id, target_field_id=f.id,
                                  confidence=1.0, approved_by="learning-engine", **kw)
            await m.insert()
            rows.append(m)
        return [_label for _label, _ in cases], await enrich_mapping_with_samples(conv, rows)

    labels, enriched = asyncio.run(run())
    got = {label: r["target_generated"] for label, r in zip(labels, enriched)}
    check("an undecided linkage column is badged", got["undecided"] is not None,
          "the analyst is told nothing and the file gets a value anyway")
    for label in ("a source column", "a fixed value", "Keep blank"):
        check(f"{label} is not badged as generated", got[label] is None,
              f"got {got[label]!r} — the glue will not run for this row")


def test_the_generated_roles_cover_every_column_the_glue_fills():
    """Drift guard. Two lists in one module — the columns `apply_to_frame` sets
    and the roles `generated_role` describes — and a column that gains one without
    the other is a screen that lies again, quietly, later."""
    import ast

    src = (_ROOT / "app" / "services" / "customer_structure_service.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "apply_to_frame")
    # Every `setcol(_find(cols, "a", "b"), …)` in the body — the needles ARE the
    # column identity here, so they are what must be covered.
    needles = []
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "setcol" and node.args):
            inner = node.args[0]
            if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_find"):
                needles.append(tuple(a.value for a in inner.args[1:]
                                     if isinstance(a, ast.Constant)))
    check("the glue fills something", bool(needles))

    from app.services.customer_structure_service import _GENERATED_ROLES
    known = {frozenset(n) for n, _why in _GENERATED_ROLES}
    for n in needles:
        check(f"{' + '.join(n)} has a role", frozenset(n) in known,
              "apply_to_frame fills this column and the grid cannot explain it")


def test_the_grid_renders_the_flag():
    """Seam. A flag nothing displays is the inert-feature failure again — the one
    this repo has hit often enough to test for by default."""
    page = _FRONTEND / "pages" / "MappingReviewPage.tsx"
    if not page.exists():
        pytest.skip("frontend sources are not present in this checkout")
    src = page.read_text(encoding="utf-8")
    check("the grid reads the flag", "target_in_load_scope === false" in src)
    check("and names the interface in the explanation",
          re.search(r"mapping\.target_sheet", src) is not None)
    check("a generated column is no longer counted as a gap",
          "!generated" in src and "const generated = m?.target_generated" in src)
    check("and the row says what will be in the file",
          "Filled by the generator" in src)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
