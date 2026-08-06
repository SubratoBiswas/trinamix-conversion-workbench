"""find_rows_for_key resolves through an indexed field_key, without dropping rows.

The store's resolver used to fetch every decision row (~2,000) and filter in Python —
a full-collection scan that cost ~15s a call on the shared instance, which is why the
startup seed could not finish (66+ calls) and every generation was slow. The fix
persists a normalised `field_key` on each row, indexes it, and queries by it.

The risk in that change is a row DISAPPEARING — the resolver's own comment calls a
vanished decision the worst outcome. These tests hold the line on it:
  * a row written normally carries field_key and is found;
  * a LEGACY row with field_key=None (pre-migration) is STILL found, because the
    query keeps a null arm until the backfill catches up;
  * the backfill stamps those legacy rows;
  * differently-spelled field names still resolve to the same key.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("mongomock_motor",
                    reason="mongomock_motor not installed — field_key index test cannot run")

from beanie import init_beanie                                    # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402

from app.models.learned import ArchivedMappingDecision, LearnedMapping  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_field_key_is_set_and_resolves_and_legacy_nulls_survive():
    async def run():
        await init_beanie(database=AsyncMongoMockClient()["fk"],
                          document_models=[LearnedMapping, ArchivedMappingDecision])
        from app.services import mapping_store as store

        # 1) A normal write carries field_key and is found by the indexed query.
        await store.record_decision(
            decision=store.SOURCE_COLUMN, target_field="Organization Name",
            value="companyname", source_erp="netsuite", captured_from="test")
        rows = await store.find_rows_for_key(target_field="Organization Name",
                                             source_erp="netsuite")
        check("the written row is found", len(rows) == 1, f"got {len(rows)}")
        check("field_key was stamped on write",
              rows[0].field_key == store.normalise_field("Organization Name"),
              f"got {rows[0].field_key!r}")

        # 2) A LEGACY row with no field_key (pre-migration) must STILL be found —
        #    otherwise the optimisation silently drops decisions.
        legacy = LearnedMapping(
            kind="example_default", category="Default Value",
            original_value="(default)", resolved_value="CUSTOMER",
            target_field="Party Usage Code", field_key=None,
            source_erp="netsuite", captured_from="legacy")
        await legacy.insert()
        found = await store.find_rows_for_key(target_field="Party Usage Code",
                                              source_erp="netsuite")
        check("a legacy null-key row is still resolved", len(found) == 1,
              f"got {len(found)}")

        # 3) The backfill stamps the legacy row.
        res = await store.backfill_field_key()
        check("backfill reports the one legacy row", res["backfilled"] == 1, f"got {res}")
        reloaded = await LearnedMapping.get(legacy.id)
        check("legacy row now carries the key",
              reloaded.field_key == store.normalise_field("Party Usage Code"),
              f"got {reloaded.field_key!r}")
        check("backfill is idempotent",
              (await store.backfill_field_key())["backfilled"] == 0)

        # 4) A different spelling of the same field resolves to the same rows.
        alias = await store.find_rows_for_key(target_field="PARTY_USAGE_CODE",
                                              source_erp="netsuite")
        check("PARTY_USAGE_CODE finds the Party Usage Code row", len(alias) == 1,
              f"got {len(alias)}")

    _run(run())


def test_the_query_narrows_to_the_asked_field():
    """The whole point: asking for one field returns only that field's rows, not the
    whole collection. (Correctness of narrowing — the speed is the same effect.)"""
    async def run():
        await init_beanie(database=AsyncMongoMockClient()["fk2"],
                          document_models=[LearnedMapping, ArchivedMappingDecision])
        from app.services import mapping_store as store
        for f, v in (("Currency", "USD"), ("Credit Hold", "N"), ("Account Type", "External")):
            await store.record_decision(decision=store.DEFAULT_VALUE, target_field=f,
                                        value=v, source_erp="netsuite", captured_from="t")
        got = await store.find_rows_for_key(target_field="Currency", source_erp="netsuite")
        check("only the Currency row comes back",
              len(got) == 1 and got[0].target_field == "Currency",
              f"got {[r.target_field for r in got]}")

    _run(run())


if __name__ == "__main__":
    test_field_key_is_set_and_resolves_and_legacy_nulls_survive()
    test_the_query_narrows_to_the_asked_field()
    print("\nfield_key index resolves correctly and drops no rows")
