"""Authoring one plain-English rule and learning it for several modules.

Pure: stdlib only. The translation itself belongs to rule_translation_service —
what is tested here is WHERE the result gets stored, which is the part that
decides whether an Item rule ever reaches BOM.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.rule_authoring_service import (  # noqa: E402
    KNOWN_OBJECTS, normalize_objects, plan_learnings,
)

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


TRANSLATED = {"rule_type": "TRIM", "config": {"source_column": "itemid"},
              "explanation": "Trim whitespace"}


# ── Module names ─────────────────────────────────────────────────────────────
def test_canonical_names_pass_through():
    ok, bad = normalize_objects(["Item", "BOM"])
    check("both recognised", ok == ["Item", "BOM"], f"got {ok}")
    check("nothing unknown", bad == [])


def test_spelling_variants_collapse_to_one_name():
    """"bom", "BOM" and "Bill of Material" must not become three rows that each
    cover a third of the conversions."""
    ok, _ = normalize_objects(["bom", "BOM", "Bill of Materials", "bill of material"])
    check("one canonical BOM", ok == ["BOM"], f"got {ok}")


def test_common_synonyms():
    ok, _ = normalize_objects(["item master", "vendor"])
    check("item master -> Item, vendor -> Supplier",
          ok == ["Item", "Supplier"], f"got {ok}")


def test_unknown_names_are_reported_not_silently_dropped():
    """A typo must surface. A learning filed under an object nothing reads is
    invisible failure."""
    ok, bad = normalize_objects(["Item", "Widgets"])
    check("good one kept", ok == ["Item"])
    check("bad one reported", bad == ["Widgets"], f"got {bad}")


def test_blank_entries_are_ignored():
    ok, bad = normalize_objects(["Item", "", "   ", None])
    check("only Item", ok == ["Item"], f"got {ok}")
    check("blanks are not 'unknown'", bad == [], f"got {bad}")


def test_catalogue_covers_the_modules_in_play():
    for o in ("Supplier", "Customer", "Item", "BOM", "Employee"):
        check(f"{o} is authorable", o in KNOWN_OBJECTS)


# ── Fan-out ──────────────────────────────────────────────────────────────────
def test_one_sentence_becomes_one_learning_per_module():
    plans = plan_learnings(translated=TRANSLATED, target_field="Item Number",
                           objects=["Item", "BOM"], description="trim the item id")
    check("two learnings", len(plans) == 2, f"got {len(plans)}")
    check("one per module", [p["target_object"] for p in plans] == ["Item", "BOM"])
    check("same rule on both",
          {p["rule_type"] for p in plans} == {"TRIM"})
    check("same target field on both",
          {p["target_field"] for p in plans} == {"Item Number"})


def test_the_analysts_sentence_is_kept_verbatim():
    """When this surfaces on another module months later, "why does BOM do
    this?" has to be answerable without reverse-engineering a rule_type."""
    plans = plan_learnings(translated=TRANSLATED, target_field="Item Number",
                           objects=["BOM"], description="  trim the item id  ")
    check("sentence recorded", "trim the item id" in plans[0]["captured_from"],
          f"got {plans[0]['captured_from']}")
    check("labelled as plain-English", "plain-English" in plans[0]["captured_from"])


def test_a_rule_with_a_source_column_is_a_column_mapping():
    plans = plan_learnings(translated=TRANSLATED, target_field="Item Number",
                           objects=["Item"], description="x")
    check("kind is column_mapping", plans[0]["kind"] == "column_mapping")
    check("source recorded", plans[0]["original_value"] == "itemid")


def test_a_rule_with_no_source_is_a_default():
    """A constant has no source column — filing it as a column mapping would put
    a mapping with no source into the library."""
    t = {"rule_type": "CONSTANT", "config": {"value": "CORPORATION"}}
    plans = plan_learnings(translated=t, target_field="Tax Organization Type",
                           objects=["Supplier"], description="always CORPORATION")
    check("kind is example_default", plans[0]["kind"] == "example_default")
    check("placeholder source", plans[0]["original_value"] == "(rule)")


def test_nothing_is_written_without_a_usable_translation():
    for t in ({}, None, {"config": {}}, {"rule_type": None}):
        check(f"no rule_type -> no plans ({t})",
              plan_learnings(translated=t, target_field="F", objects=["Item"],
                             description="x") == [])


def test_nothing_is_written_without_a_target_field_or_module():
    check("no target field",
          plan_learnings(translated=TRANSLATED, target_field="", objects=["Item"],
                         description="x") == [])
    check("no modules",
          plan_learnings(translated=TRANSLATED, target_field="F", objects=[],
                         description="x") == [])


def test_config_is_shared_not_copied_per_module():
    """Each module must carry the SAME definition — divergent configs would make
    'the rule' mean different things depending on where you read it."""
    plans = plan_learnings(translated=TRANSLATED, target_field="Item Number",
                           objects=["Item", "BOM", "Supplier"], description="x")
    configs = [p["rule_config"] for p in plans]
    check("all identical", all(c == configs[0] for c in configs), f"got {configs}")


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
