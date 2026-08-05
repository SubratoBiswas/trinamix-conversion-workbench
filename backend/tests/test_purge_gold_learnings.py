"""Remove every gold-captured learning in one action, reversibly.

Analyst, 05-Aug: "How to remove all the gold output learning till today." Gold
uploads seed the store with example_default / suppress_field / column_mapping rows
stamped captured_from="gold example"; they auto-apply forever, which is why a
field-wide default the analyst keeps clearing keeps coming back. This purges them
across all objects — archived first, so it is recoverable — and leaves everything
else untouched.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the gold-purge check cannot run")

from beanie import init_beanie                                   # noqa: E402
from mongomock_motor import AsyncMongoMockClient                 # noqa: E402

from app.models.learned import ArchivedMappingDecision, LearnedMapping  # noqa: E402

_MODELS = [LearnedMapping, ArchivedMappingDecision]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


async def _seed():
    # Three gold learnings across two objects, and two NON-gold rows that must survive.
    await LearnedMapping(kind="example_default", category="Default Value", original_value="(default)",
                         target_object="Customer", target_field="Insert Update Indicator",
                         resolved_value="I", captured_from="gold example").insert()
    await LearnedMapping(kind="suppress_field", category="Left blank", original_value="(blank)",
                         resolved_value="",
                         target_object="Customer", target_field="Batch Identifier",
                         captured_from="gold example").insert()
    await LearnedMapping(kind="example_default", category="Default Value", original_value="(default)",
                         target_object="Employee HDL", target_field="EffectiveStartDate",
                         resolved_value="1900/1/1", captured_from="gold example").insert()
    # A human decision and a non-gold seed — kept.
    await LearnedMapping(kind="example_default", category="Default Value", original_value="(default)",
                         target_object="Customer", target_field="Payment Terms",
                         resolved_value="Net 30", captured_from="kept in Mapping Review",
                         captured_by="analyst@x.com").insert()
    await LearnedMapping(kind="column_mapping", category="Alias", original_value="src",
                         target_object="Supplier", target_field="Supplier Name",
                         resolved_value="Supplier Name", captured_from="workbook").insert()


def test_dry_run_reports_and_changes_nothing():
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["gold_purge_dry"],
                          document_models=_MODELS)
        from app.services.gold_library_service import purge_gold_learnings
        await _seed()
        res = await purge_gold_learnings(dry_run=True)
        check("found the three gold rows", res["gold_learnings_found"] == 3,
              f"got {res['gold_learnings_found']}")
        check("removed nothing on a dry run", res["removed"] == 0)
        check("by_kind breaks them down",
              res["by_kind"].get("example_default") == 2
              and res["by_kind"].get("suppress_field") == 1, f"got {res['by_kind']}")
        live = await LearnedMapping.find({"captured_from": "gold example"}).to_list()
        check("all three are still there", len(live) == 3, f"got {len(live)}")
        return True

    check("end to end", asyncio.run(run()))


def test_purge_removes_only_gold_and_archives_them():
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["gold_purge_do"],
                          document_models=_MODELS)
        from app.services.gold_library_service import purge_gold_learnings
        await _seed()
        res = await purge_gold_learnings(dry_run=False)
        check("removed the three gold rows", res["removed"] == 3, f"got {res['removed']}")

        gold_left = await LearnedMapping.find({"captured_from": "gold example"}).to_list()
        check("no gold learning remains", not gold_left, f"got {len(gold_left)}")

        survivors = await LearnedMapping.find_all().to_list()
        fields = {s.target_field for s in survivors}
        check("the human decision survived", "Payment Terms" in fields)
        check("the workbook mapping survived", "Supplier Name" in fields)
        check("nothing else was taken", len(survivors) == 2, f"got {len(survivors)}")

        archived = await ArchivedMappingDecision.find(
            {"reason": "gold-purge"}).to_list()
        check("the three are archived, recoverable", len(archived) == 3,
              f"got {len(archived)}")
        return True

    check("end to end", asyncio.run(run()))


def test_purge_is_idempotent():
    import asyncio

    async def run():
        await init_beanie(database=AsyncMongoMockClient()["gold_purge_idem"],
                          document_models=_MODELS)
        from app.services.gold_library_service import purge_gold_learnings
        await _seed()
        await purge_gold_learnings(dry_run=False)
        again = await purge_gold_learnings(dry_run=False)
        check("a second purge finds nothing", again["gold_learnings_found"] == 0
              and again["removed"] == 0, f"got {again}")
        return True

    check("end to end", asyncio.run(run()))


if __name__ == "__main__":
    test_dry_run_reports_and_changes_nothing()
    test_purge_removes_only_gold_and_archives_them()
    test_purge_is_idempotent()
    print("\ngold purge holds")
