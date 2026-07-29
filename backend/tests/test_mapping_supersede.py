"""Which mapping-document rows a newer file retires when it takes over.

The Beanie parts of ``supersede_previous`` need Mongo, so what is tested here is
the DECISION it makes: given the rows an older document asserted and the rows a
newer one asserts, which pairs stop applying. That is the rule an analyst relies
on, and getting it wrong either leaves stale mappings in force or deletes the new
values that just replaced them.

Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def pair(obj, field):
    return (obj.strip().lower(), field.strip().lower())


def asserted(rows):
    """Mirrors _asserted_pairs: what a document actually puts into force."""
    out = set()
    for r in rows:
        if r.get("decision") == "rejected":
            continue
        if r.get("status") == "conflict" and r.get("decision") != "approved":
            continue
        if r.get("target_object") and r.get("target_field"):
            out.add(pair(r["target_object"], r["target_field"]))
    return out


def retired(old_rows, new_rows):
    """Pairs the older document asserted that the newer one does not."""
    keep = asserted(new_rows)
    return {pair(r["target_object"], r["target_field"]) for r in old_rows
            if r.get("target_object") and r.get("target_field")} - keep


def row(obj, field, status="new", decision="pending"):
    return {"target_object": obj, "target_field": field,
            "status": status, "decision": decision}


# ── What a document puts into force ──────────────────────────────────────────
def test_new_and_unchanged_rows_apply_without_ticking():
    rows = [row("Supplier", "Supplier Name"),
            row("Supplier", "Tax Organization Type", status="unchanged")]
    check("both count as asserted", asserted(rows) == {
        pair("Supplier", "Supplier Name"),
        pair("Supplier", "Tax Organization Type")})


def test_unreviewed_conflicts_are_not_asserted():
    """A contradiction is never applied silently — so it also cannot be the
    reason an older mapping survives."""
    rows = [row("Supplier", "Alternate Name", status="conflict")]
    check("pending conflict asserts nothing", asserted(rows) == set())
    rows = [row("Supplier", "Alternate Name", status="conflict", decision="approved")]
    check("approved conflict asserts", asserted(rows) == {pair("Supplier", "Alternate Name")})


def test_rejected_rows_are_not_asserted():
    rows = [row("Supplier", "Supplier Name", decision="rejected")]
    check("rejected asserts nothing", asserted(rows) == set())


# ── What supersession retires ────────────────────────────────────────────────
def test_a_field_dropped_by_the_new_file_is_retired():
    """The whole point: v1 mapped Customer Number, v2 does not mention it. Left
    alone it keeps applying and the tool looks updated while shipping v1 rules."""
    old = [row("Supplier", "Supplier Name"), row("Supplier", "Customer Number")]
    new = [row("Supplier", "Supplier Name")]
    check("the dropped field is retired",
          retired(old, new) == {pair("Supplier", "Customer Number")},
          f"got {retired(old, new)}")


def test_a_field_restated_by_the_new_file_is_kept():
    """apply_proposal already updated it in place — retiring it here would delete
    the value that just replaced the old one."""
    old = [row("Supplier", "Supplier Name")]
    new = [row("Supplier", "Supplier Name", status="conflict", decision="approved")]
    check("restated field survives", retired(old, new) == set(), f"got {retired(old, new)}")


def test_case_and_spacing_do_not_cause_a_false_retire():
    old = [row("Supplier", "Supplier Name")]
    new = [row("supplier ", "  supplier name")]
    check("same pair despite spelling", retired(old, new) == set(), f"got {retired(old, new)}")


def test_an_unreviewed_conflict_in_the_new_file_retires_the_old_row():
    """If the new document raises a conflict and nobody approves it, the new
    value is NOT in force — and neither should the old one be. Leaving the old
    row applying would mean an unreviewed contradiction silently resolved in
    favour of the superseded file."""
    old = [row("Supplier", "Alternate Name")]
    new = [row("Supplier", "Alternate Name", status="conflict")]
    check("old row is retired", retired(old, new) == {pair("Supplier", "Alternate Name")},
          f"got {retired(old, new)}")


def test_other_modules_are_untouched():
    old = [row("Customer", "Account Number"), row("Supplier", "Supplier Name")]
    new = [row("Supplier", "Supplier Name")]
    r = retired(old, new)
    check("a different module's field is reported",
          r == {pair("Customer", "Account Number")}, f"got {r}")
    # supersede_previous only considers older documents whose objects INTERSECT
    # the new one's, so a Customer-only document is never touched by a Supplier
    # upload — this asserts the pair maths, the intersection guard is separate.


def test_nothing_retired_when_the_new_file_is_a_superset():
    old = [row("Supplier", "Supplier Name")]
    new = [row("Supplier", "Supplier Name"), row("Supplier", "Tax Organization Type")]
    check("a growing document retires nothing", retired(old, new) == set())


def test_everything_retired_when_the_new_file_shares_nothing():
    old = [row("Supplier", "A"), row("Supplier", "B")]
    new = [row("Supplier", "C")]
    check("all old pairs retired",
          retired(old, new) == {pair("Supplier", "A"), pair("Supplier", "B")})


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
