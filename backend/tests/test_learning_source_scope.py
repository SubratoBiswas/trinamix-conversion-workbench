"""Learnings are keyed by SOURCE SYSTEM, not just client + object + field.

Item maps differently out of NetSuite than out of SyteLine. Before this, the two
shared one row: the second capture overwrote the first, and whichever survived
was handed to both conversions.

The Beanie parts need Mongo, so what is tested here is the query SHAPE and the
precedence rule — the two things that decide whether a SyteLine conversion can
see a NetSuite mapping.

Unlike the other suites in this directory this one is NOT standalone: importing
``learning_service`` pulls in beanie, so run it with pytest against the app's
dependencies rather than `python3 tests/test_learning_source_scope.py`.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.learning_service import source_scope  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


class Row:
    """Stand-in for a LearnedMapping row."""

    def __init__(self, target_field, source_erp, original_value=""):
        self.target_field = target_field
        self.source_erp = source_erp
        self.original_value = original_value


def matches(row, scope):
    """Does this row satisfy the scope filter? Mirrors what Mongo would do."""
    if not scope:
        return True
    for clause in scope["$or"]:
        want = clause["source_erp"]
        if isinstance(want, dict):                 # {"$exists": False}
            if row.source_erp is None:
                return True
        elif row.source_erp == want:
            return True
    return False


def prefer_exact(rows, source):
    """Mirrors the precedence in apply_learned_to_conversion: an exact source
    match beats a legacy untagged row for the same target field."""
    if not source:
        return rows
    exact = {r.target_field for r in rows if r.source_erp == source}
    return [r for r in rows
            if r.source_erp == source or r.target_field not in exact]


# ── The filter ───────────────────────────────────────────────────────────────
def test_scope_is_empty_when_source_is_unknown():
    """No source resolved must mean no filtering — never an empty result set."""
    check("no filter", source_scope(None) == {})
    check("blank is the same", source_scope("") == {})


def test_scope_admits_its_own_source():
    s = source_scope("netsuite")
    check("netsuite row matches", matches(Row("Item Number", "netsuite"), s))
    check("syteline row does NOT", not matches(Row("Item Number", "syteline"), s))


def test_scope_admits_legacy_untagged_rows():
    """Every learning captured before this scoping has no source_erp. Filtering
    them out would strand the entire existing library on the first deploy."""
    s = source_scope("netsuite")
    check("untagged row still visible", matches(Row("Item Number", None), s))


def test_a_third_source_is_excluded():
    s = source_scope("syteline")
    for other in ("netsuite", "oracle_ebs", "custom"):
        check(f"{other} excluded", not matches(Row("Item Number", other), s))
    check("syteline admitted", matches(Row("Item Number", "syteline"), s))


# ── Precedence ───────────────────────────────────────────────────────────────
def test_exact_source_beats_untagged_for_the_same_field():
    rows = [Row("Item Number", None, "legacy_col"),
            Row("Item Number", "netsuite", "itemid")]
    kept = prefer_exact(rows, "netsuite")
    check("only the netsuite row survives", len(kept) == 1, f"got {len(kept)}")
    check("and it is the right one", kept[0].original_value == "itemid")


def test_untagged_still_used_where_no_source_specific_rule_exists():
    """Coverage must not drop: an untagged rule for a field nothing has claimed
    is still the best answer available."""
    rows = [Row("Item Number", "netsuite", "itemid"),
            Row("Item Description", None, "salesdescription")]
    kept = prefer_exact(rows, "netsuite")
    check("both kept", len(kept) == 2, f"got {len(kept)}")
    check("the untagged one is the description rule",
          any(r.target_field == "Item Description" for r in kept))


def test_netsuite_and_syteline_item_rules_coexist():
    """The case the analyst raised, end to end."""
    library = [Row("Item Number", "netsuite", "itemid"),
               Row("Item Number", "syteline", "item"),
               Row("Item Number", None, "legacy_item_no")]

    ns = prefer_exact([r for r in library if matches(r, source_scope("netsuite"))],
                      "netsuite")
    check("NetSuite sees one rule", len(ns) == 1, f"got {[r.original_value for r in ns]}")
    check("and it is itemid", ns[0].original_value == "itemid")

    sl = prefer_exact([r for r in library if matches(r, source_scope("syteline"))],
                      "syteline")
    check("SyteLine sees one rule", len(sl) == 1, f"got {[r.original_value for r in sl]}")
    check("and it is item", sl[0].original_value == "item")


def test_no_source_sees_everything():
    """A conversion whose source cannot be resolved keeps the old behaviour
    rather than silently losing its mappings."""
    library = [Row("Item Number", "netsuite", "itemid"),
               Row("Item Number", "syteline", "item")]
    kept = [r for r in library if matches(r, source_scope(None))]
    check("unfiltered", len(kept) == 2)


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
