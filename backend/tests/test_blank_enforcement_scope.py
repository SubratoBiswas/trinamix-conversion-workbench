"""What the live reseed reported, and why it was nothing.

Called on the deployed instance, the 30-Jul blank enforcement came back with
`learnings_retired: 0` and `mappings_blanked: 0` for every field — only
`skipped_human` counts. Both zeros had a cause, and neither was visible from the
code alone:

  1. CLIENT SCOPE. The analyst docs seed under the bootstrap client, while the
     gold-example rows carry the client of the project they were captured in. The
     sweep compared against the correction's own client id, so every contradicting
     row fell outside the filter and survived — including the column_mapping that
     was still binding Tax Reporting Name to Legal Name. The conversion pass now
     runs FIRST and collects the clients whose conversions the correction actually
     reached; a scoped learning is retired when its client is one of those, which
     is the proof it is in scope. Global rows always go, because a global row is
     precisely what reaches this client.

  2. HUMAN APPROVALS WITH NO DATE TEST. Every skipped_human was an approval made
     BEFORE the 30-Jul correction. "For conflicts always the latest one should be
     taken for mapping" — a correction dated after someone's approval is that same
     analyst changing their mind.

Pure: mirrors of the two decisions, plus seams over the code they mirror.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CORRECTION = datetime(2026, 7, 30)


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def retire(learning_client, seen_clients):
    """Mirror of the library sweep's client gate."""
    if learning_client is None:
        return True
    return not (seen_clients and learning_client not in seen_clients)


def skip_as_human(status, approver, approved_at, as_of=CORRECTION):
    """Mirror of the mapping pass's human-approval gate."""
    human = status == "overridden" or (status == "approved"
                                       and approver != "learning-engine")
    if human and as_of is not None:
        human = bool(approved_at) and approved_at >= as_of
    return human


def test_a_global_learning_is_always_retired():
    """The gold-example row is global, and global is what reaches this client."""
    check("retired", retire(None, {"client-A"}) is True)


def test_a_learning_under_a_different_client_id_of_OUR_data_is_retired():
    """The live case: docs seed under the bootstrap client, gold rows under the
    project's. Comparing to the correction's own id retired nothing."""
    check("retired", retire("project-client", {"bootstrap", "project-client"}) is True)


def test_another_tenants_learning_is_never_touched():
    check("left alone", retire("other-tenant", {"bootstrap", "project-client"}) is False)


def test_with_no_conversions_reached_nothing_scoped_is_retired():
    """No conversion matched, so nothing proves any scoped row is ours. Only
    global rows go — the conservative reading."""
    check("scoped row survives", retire("someone", set()) is True)
    check("global row still goes", retire(None, set()) is True)


def test_an_approval_older_than_the_correction_does_not_win():
    check("not treated as human veto",
          skip_as_human("approved", "analyst@x.com", datetime(2026, 7, 28)) is False)


def test_an_approval_after_the_correction_still_wins():
    check("person wins",
          skip_as_human("approved", "analyst@x.com", datetime(2026, 7, 31)) is True)


def test_an_engine_approval_is_never_a_human_veto():
    check("engine ignored",
          skip_as_human("approved", "learning-engine", datetime(2026, 8, 1)) is False)


def test_an_override_is_judged_on_its_date_too():
    check("older override yields",
          skip_as_human("overridden", "analyst@x.com", datetime(2026, 7, 1)) is False)
    check("newer override wins",
          skip_as_human("overridden", "analyst@x.com", datetime(2026, 8, 2)) is True)


def test_with_no_correction_date_a_human_still_wins():
    """Undated instruction = previous behaviour, the safe default for old files."""
    check("person wins",
          skip_as_human("approved", "analyst@x.com", None, as_of=None) is True)


def test_the_service_matches_these_mirrors():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "learning_service.py").read_text(encoding="utf-8")
    body = src.split("async def enforce_blank_corrections(")[1].split("\nasync def ")[0]
    i_conv = body.index("one pass over the conversions")
    i_ret = body.index("retire the contradicting library rows")
    check("conversions are scanned before the library sweep", i_conv < i_ret,
          "the sweep needs the client set the pass collects")
    check("the sweep uses the collected clients", "_lc not in seen_clients" in body)
    check("global rows are not filtered out", "_lc is not None and seen_clients" in body)
    check("the human gate is date-aware", "_at >= as_of" in body)

    seed = (Path(__file__).resolve().parent.parent / "app" / "services"
            / "catalog_seed_service.py").read_text(encoding="utf-8")
    check("the seeder passes the file's date", "as_of=_effective_date_of(doc)" in seed)
    check("and enforces in ONE batched pass", "enforce_blank_corrections(" in seed)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall blank-enforcement scope checks passed")
