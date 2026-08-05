"""Keep blank must answer the click, not sit on the fleet fan-out.

WHAT WAS REPORTED
-----------------
"The keep blank button does not do anything." It did fire, and it did work — the
row flipped to not_applicable server-side. What it did not do was RETURN in time:

  * `keep_blank` applied the decision to this row and its siblings (fast), recorded
    the suppression learning (fast), then AWAITED
    `propagate_learning_to_open_conversions`, which walks every conversion in the
    fleet — 35 today — with several Atlas round-trips each.
  * The browser's axios instance times out at 60s. The fan-out routinely took
    longer, so the reply that refreshes the grid never arrived. A click that
    worked looked dead.

THE FIX, AND WHAT THIS TEST PINS
--------------------------------
The fan-out moved to a FastAPI background task — it still runs, just AFTER the
response. So:

  * the endpoint returns the row already blank, without awaiting the fan-out;
  * the fan-out is SCHEDULED, so the other conversions still get the decision.

Both are asserted. The first is the bug; the second is the thing the first must
not have broken.

Calls the endpoint FUNCTION directly against mongomock — the full app needs a
reachable Mongo (test_app.py skips without one), and the unit here is the handler,
not the wiring.
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the keep-blank timing check cannot run",
)

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402
from starlette.background import BackgroundTasks                 # noqa: E402

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


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


async def _seed():
    template = FBDITemplate(name="Customer Import", business_object="Customer",
                            file_name="t.xlsm", status="parsed", is_global=True)
    await template.insert()
    sheet = FBDISheet(template_id=template.id, sheet_name="HZ_IMP_PARTIES_T",
                      sequence=1, field_count=1)
    await sheet.insert()
    field = FBDIField(template_id=template.id, sheet_id=sheet.id,
                      field_name="Batch Identifier", display_name="*Batch Identifier",
                      sequence=1)
    await field.insert()

    client = Client(name="C", code="C", is_default=True); await client.insert()
    project = Project(name="P", client_id=client.id); await project.insert()
    conv = Conversion(project_id=project.id, name="c", template_id=template.id,
                      target_object="Customer", source_type="dataset")
    await conv.insert()
    m = MappingSuggestion(conversion_id=conv.id, target_field_id=field.id,
                          default_value="CONV-E3F9D5", confidence=1.0,
                          status="approved", approved_by="admin@trinamix.com")
    await m.insert()
    return m


def test_keep_blank_applies_the_row_and_defers_the_fanout():
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["keep_blank_fast"],
                          document_models=_MODELS)
        from app.routers.mapping import keep_blank

        m = await _seed()
        bt = BackgroundTasks()
        user = User(name="Analyst", email="analyst@trinamix.com",
                    password_hash="x", role="admin")

        t0 = time.monotonic()
        out = await keep_blank(str(m.id), bt, user=user)
        elapsed = time.monotonic() - t0

        # The row the analyst pressed is blank in the reply — no waiting on anything.
        check("the returned row is not_applicable", out["status"] == "not_applicable",
              f"got {out['status']!r}")
        check("its source is cleared", not out.get("source_column"))
        check("its default is cleared", not out.get("default_value"))

        # And it is blank in the store, so a reload shows the same thing.
        again = await MappingSuggestion.get(m.id)
        check("the stored row is not_applicable too", again.status == "not_applicable")
        check("with no default left", not again.default_value)

        # The fan-out did NOT run inside the call — it is queued as a background task.
        funcs = [getattr(t, "func", None) for t in bt.tasks]
        names = [getattr(f, "__name__", "") for f in funcs]
        check("a background task was scheduled", bool(bt.tasks),
              "nothing was deferred — the fan-out is still inline")
        check("and it is the propagation helper", "_propagate_in_background" in names,
              f"scheduled {names}")

        # A generous ceiling. The point is not a benchmark; it is that the handler
        # returns without the fleet walk. The walk itself is not even reachable here
        # (one conversion), so anything approaching the old minute would mean the
        # await crept back in.
        check("the handler returned promptly", elapsed < 5.0,
              f"took {elapsed:.1f}s — the fan-out is being awaited again")

        # The deferred task, when run, actually propagates without error.
        await bt()
        return True

    check("end to end", asyncio.run(run()))


if __name__ == "__main__":
    test_keep_blank_applies_the_row_and_defers_the_fanout()
    print("\nkeep-blank returns fast and defers the fan-out")
