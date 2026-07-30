"""Why 1,256 of 1,365 Item fields came back "No confident match found".

CW_Issues 2, row 5 (Tejaswini): "The confidence scores for the suggested mappings
on an average is below 50. Scope to improve suggestions as most of them only check
type compatibility and keyword check." Sheet4 of that workbook counts the reasons —
1,256 of 1,365 are "No confident match found".

It read as a scoring weakness. It was arithmetic. ``suggest_mappings`` is called
ONCE with every field of every sheet (1,365 for Item across 17 sheets, ~1,250 for
Customer across 19), and it kept a single ``used_sources`` set for the whole call.
A source column could therefore be spent on exactly one target in the entire
template, so the number of targets that could EVER be mapped was capped at the
number of source columns — a few hundred. The residual is the reported number.

The second-order damage was worse than the count. Oracle REQUIRES the same key on
every sheet; the matcher spent Item Number on the first and left the other sixteen
empty. The analyst filed that separately, from the other end, as "id should be
mapped to Party Original System Reference in all sheets, except ...".

Scoping the set per sheet keeps the property the rule exists for — inside one
sheet, one column should not be smeared across several unrelated fields — and drops
the one it never intended.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ai.base import SourceColumn, TargetField          # noqa: E402
from app.ai.rule_based import RuleBasedMapper              # noqa: E402

SHEETS = ["EGP_SYSTEM_ITEMS", "EGP_ITEM_REVISIONS", "EGP_ITEM_CATEGORIES",
          "EGP_ITEM_ORGS", "EGP_ITEM_TRANSLATIONS"]
FIELDS = ["Item Number", "Description", "Primary UOM"]


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def src(name, samples=None, t="string"):
    return SourceColumn(name=name, inferred_type=t, null_percent=0.0,
                        sample_values=samples or ["x"], distinct_count=10)


def fan_out_targets():
    out, i = [], 0
    for sheet in SHEETS:
        for fname in FIELDS:
            i += 1
            out.append(TargetField(id=str(i), field_name=fname, description=None,
                                   data_type="string", max_length=100,
                                   required=(fname == "Item Number"),
                                   sheet_id=sheet))
    return out


SOURCES = [src("item_number", ["A-100", "A-101"]),
           src("description", ["Widget"]),
           src("uom", ["EA"])]


def test_the_same_key_is_mapped_on_every_sheet():
    targets = fan_out_targets()
    res = RuleBasedMapper().suggest_mappings(SOURCES, targets)
    by_id = {t.id: t for t in targets}
    mapped = [r for r in res if r.source_column]
    check("every target is mapped", len(mapped) == len(targets),
          f"got {len(mapped)}/{len(targets)}")
    keys = {by_id[r.target_field_id].sheet_id
            for r in res if r.target_field_name == "Item Number" and r.source_column}
    check("Item Number resolves on all 5 sheets", keys == set(SHEETS), f"got {sorted(keys)}")


def test_the_old_global_set_would_have_left_twelve_of_fifteen_unmapped():
    """The arithmetic, stated as a test so the size of the fix is on the record."""
    n_sources, n_targets = len(SOURCES), len(fan_out_targets())
    old_ceiling = min(n_sources, n_targets)      # one source, one target, template-wide
    check("the old ceiling was the source count", old_ceiling == 3)
    check("so 12 of 15 could never be mapped", n_targets - old_ceiling == 12)
    res = RuleBasedMapper().suggest_mappings(SOURCES, fan_out_targets())
    check("and the matcher now beats that ceiling",
          len([r for r in res if r.source_column]) > old_ceiling)


def test_within_one_sheet_a_column_is_still_not_smeared():
    """The property the rule exists for has to survive: on a single sheet, one
    source column must not win several unrelated targets."""
    targets = [TargetField(id="1", field_name="Item Number", description=None,
                           data_type="string", max_length=100, required=True,
                           sheet_id="S1"),
               TargetField(id="2", field_name="Item Number Alt", description=None,
                           data_type="string", max_length=100, required=False,
                           sheet_id="S1")]
    res = RuleBasedMapper().suggest_mappings([src("item_number", ["A-1"])], targets)
    used = [r.source_column for r in res if r.source_column]
    check("the column is used once on this sheet", len(used) == 1, f"got {used}")


def test_fields_with_no_sheet_still_behave_as_one_group():
    """Templates that carry no sheet id must keep exactly the old behaviour."""
    targets = [TargetField(id="1", field_name="Item Number", description=None,
                           data_type="string", max_length=100, required=True),
               TargetField(id="2", field_name="Item Number Alt", description=None,
                           data_type="string", max_length=100, required=False)]
    res = RuleBasedMapper().suggest_mappings([src("item_number", ["A-1"])], targets)
    check("still one use", len([r for r in res if r.source_column]) == 1)


def test_the_sheet_reaches_the_matcher_from_the_database():
    """Seam: the scoping is worthless if _target_fields_for drops the sheet."""
    from pathlib import Path
    svc = (Path(__file__).resolve().parent.parent / "app" / "services"
           / "mapping_service.py").read_text(encoding="utf-8")
    check("_target_fields_for carries sheet_id", 'sheet_id=str(getattr(f, "sheet_id"' in svc)
    base = (Path(__file__).resolve().parent.parent / "app" / "ai"
            / "base.py").read_text(encoding="utf-8")
    check("TargetField declares it", "sheet_id: str | None = None" in base)
    rb = (Path(__file__).resolve().parent.parent / "app" / "ai"
          / "rule_based.py").read_text(encoding="utf-8")
    check("the matcher keys the used-set by sheet", "used_by_sheet" in rb)
    check("and no global used_sources set survives",
          "used_sources: set[str] = set()" not in rb)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall matcher sheet-scope checks passed")
