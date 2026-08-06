"""The 03-Aug Customer seed must actually LAND its constants and blanks in the store.

For a long time it did not. `seed_customer_mapping_03aug` tallied its work with
``counts[DECISION_TO_KIND[decision]] += 1`` but initialised ``counts`` with the
DECISION names ("default_value", "suppress") instead of the KIND names
("example_default", "suppress_field") that DECISION_TO_KIND returns. So the first
``constant`` row raised KeyError, the exception aborted the whole seed, and every
default and suppression after the derive/rule rows — Payment Terms, Role Type,
Insert Update Indicator, Batch Identifier, and later the 06-Aug derived constants —
never reached the store. They shipped only through the write-time overlay; the
Learning Centre never saw them, and they were never client-scoped propagating rows.

This drives the REAL seed against mongomock and asserts the constants and blanks are
present, client-scoped (project_id is None → applies to every conversion of the
object), and that Insert Update Indicator lands as the two per-sheet rows.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip(
    "mongomock_motor",
    reason="mongomock_motor is not installed — the seed-persistence check cannot run",
)

from beanie import init_beanie                                    # noqa: E402
from mongomock_motor import AsyncMongoMockClient                  # noqa: E402

from app.models.client import Client                              # noqa: E402
from app.models.conversion import Conversion                      # noqa: E402
from app.models.dataset import Dataset, DatasetColumnProfile      # noqa: E402
from app.models.fbdi import (                                     # noqa: E402
    FBDIField, FBDISheet, FBDITemplate, FBDITemplateFile, GoldStandard, OracleLookup,
)
from app.models.learned import ArchivedMappingDecision, LearnedMapping  # noqa: E402
from app.models.mapping import MappingSuggestion                  # noqa: E402
from app.models.output import ConvertedOutput                     # noqa: E402
from app.models.project import Project                            # noqa: E402
from app.models.transformation import Crosswalk, TransformationRule  # noqa: E402
from app.models.user import User                                  # noqa: E402

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


def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


def test_the_seed_lands_constants_and_blanks_client_scoped():
    async def run():
        await init_beanie(database=AsyncMongoMockClient()["seed_persist"],
                          document_models=_MODELS)
        from app.services.catalog_seed_service import seed_customer_mapping_03aug

        res = await seed_customer_mapping_03aug()

        # 1) It ran to completion — no KeyError abort — and recorded every kind.
        check("no rows skipped or retired", res.get("skipped") == 0 and res.get("retired") == 0,
              f"got {res}")
        check("constants were recorded", res.get("example_default", 0) >= 25, f"got {res}")
        check("blanks were recorded", res.get("suppress_field", 0) >= 2, f"got {res}")
        check("mappings and rules still recorded",
              res.get("column_mapping", 0) >= 20 and res.get("rule", 0) >= 15, f"got {res}")

        rows = await LearnedMapping.find_all().to_list()

        def by(field):
            return [r for r in rows if r.target_field == field]

        # 2) The named constants that used to vanish are present with the right value.
        for field, val in (("Party Usage Code", "CUSTOMER"), ("Currency", "USD"),
                           ("Payment Terms", "IMMEDIATE"), ("Role Type", "CONTACT"),
                           ("Object Party Type", "ORGANIZATION"),
                           ("Responsibility Type", "BILL_TO")):
            rs = [r for r in by(field) if r.kind == "example_default"]
            check(f"{field} = {val} is stored", any(r.resolved_value == val for r in rs),
                  f"got {[(r.kind, r.resolved_value) for r in by(field)]}")

        # 3) They are CLIENT-scoped, not project-scoped — so they reach every
        #    conversion of the object, current and future.
        pu = by("Party Usage Code")[0]
        check("client-scoped (a client id is set)", pu.client_id is not None)
        check("not pinned to one project", getattr(pu, "project_id", None) is None,
              f"got project_id={getattr(pu, 'project_id', None)}")

        # 4) Batch Identifier is a suppression.
        bi = [r for r in by("Batch Identifier") if r.kind == "suppress_field"]
        check("Batch Identifier is suppressed", len(bi) == 1, f"got {by('Batch Identifier')}")

        # 5) Insert Update Indicator is TWO rows: I on profiles, blank everywhere else.
        iui = by("Insert Update Indicator")
        i_on = [r for r in iui if r.kind == "example_default" and r.resolved_value == "I"]
        blank = [r for r in iui if r.kind == "suppress_field"]
        check("I is on the profiles sheet only",
              len(i_on) == 1 and i_on[0].sheets == ["RA_CUSTOMER_PROFILES_INT_ALL"],
              f"got {[(r.kind, r.sheets, r.exclude_sheets) for r in iui]}")
        check("blank excludes the profiles sheet",
              len(blank) == 1 and blank[0].exclude_sheets == ["RA_CUSTOMER_PROFILES_INT_ALL"],
              f"got {[(r.kind, r.sheets, r.exclude_sheets) for r in iui]}")

    _run(run())


if __name__ == "__main__":
    test_the_seed_lands_constants_and_blanks_client_scoped()
    print("\nthe 03-Aug seed lands its constants and blanks, client-scoped")
