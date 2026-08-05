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
    check("and it defaults to in-scope, so a silent None never reads as a warning",
          MappingOut.model_fields["target_in_load_scope"].default is True)


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


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
