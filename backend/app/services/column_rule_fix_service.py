"""Turn a column-rule finding into the rule that fixes it.

The Cleansing tab now tells the analyst exactly what Oracle will reject — "Inactive
Date: 3,872 of 3,872 rows are not in YYYY/MM/DD", "Taxpayer Country: 19 values are
longer than VARCHAR2(2)". Reading it is only half the job: every one of those has a
single obvious remedy, and making the analyst go and hand-build the same rule in
another screen is asking them to retype something the tool already knows.

WHAT EACH FINDING BECOMES
-------------------------
  date_format      -> DATE_FORMAT into the mask the template states
  max_length       -> SUBSTRING to the stated limit
  numeric          -> REMOVE_SPECIAL_CHARS, keeping digits, sign and decimal point
  scale            -> NUMBER_FORMAT at the stated number of decimals
  do_not_populate  -> CONSTANT "" — Oracle says the column is not used, so ship it blank
  value_set        -> NOT auto-fixable: which accepted code a bad value maps to is a
                      business decision. Routed to the crosswalk instead.
  precision        -> NOT auto-fixable: a number too big for its column is almost always
                      a mis-mapped source, and truncating digits would hide that.
  mandatory        -> NOT auto-fixable: nothing here can invent a value. Needs a mapping
                      or a default, which is the analyst's call.

The refusals matter as much as the fixes. A one-click button that quietly truncates an
over-long number, or picks a code on the analyst's behalf, would turn a visible problem
into an invisible one — which is the failure this whole panel exists to prevent.

Pure: stdlib only, so the mapping from finding to rule is testable without a DB.
"""
from __future__ import annotations

from typing import Any, Optional

# Rules we can build from the template's own statement, with no judgement call.
AUTO_FIXABLE = {"date_format", "max_length", "numeric", "scale", "do_not_populate"}

# Why each of the others needs a person. Shown in the UI on the disabled button, so
# "no Fix button" never reads as "the tool forgot".
NOT_AUTO_FIXABLE = {
    "value_set": ("Which accepted code a wrong value should become is a business "
                  "decision — open the value crosswalk for this field."),
    "precision": ("A number too long for its column is nearly always a mis-mapped "
                  "source column. Truncating the digits would hide that; check the "
                  "mapping first."),
    "mandatory": ("Nothing can invent a value. Map a source column or set a default "
                  "for this field."),
}


def plan_fix(finding: dict) -> dict:
    """``{ok, rule_type, rule_config, description}`` for one finding.

    Returns ``{"ok": False, "reason": ...}`` when the remedy needs a person — the
    caller shows the reason rather than a button.
    """
    rule = str(finding.get("rule") or "")
    field = str(finding.get("field") or "")
    if not field:
        return {"ok": False, "reason": "The finding names no column."}
    if rule in NOT_AUTO_FIXABLE:
        return {"ok": False, "reason": NOT_AUTO_FIXABLE[rule], "rule": rule}
    if rule not in AUTO_FIXABLE:
        return {"ok": False, "reason": f"No automatic fix for {rule!r}.", "rule": rule}

    if rule == "date_format":
        mask = str(finding.get("format_mask") or "YYYY/MM/DD")
        out = (mask.upper().replace("YYYY", "%Y").replace("MM", "%m")
                   .replace("DD", "%d"))
        return {
            "ok": True, "rule_type": "DATE_FORMAT",
            # No input_format: the engine's DATE_FORMAT leaves a value it cannot parse
            # alone rather than mangling it, and to_fbdi_date already accepts the
            # spellings these extracts actually carry.
            "rule_config": {"output_format": out},
            "description": f"{field}: reformat dates to {mask} (from the template's "
                           f"own comment).",
        }
    if rule == "max_length":
        limit = int(finding.get("limit") or 0)
        if limit <= 0:
            return {"ok": False, "reason": "The finding carries no length limit."}
        return {
            "ok": True, "rule_type": "SUBSTRING",
            "rule_config": {"start": 0, "length": limit},
            "description": f"{field}: trim to the column's {limit} characters. Check "
                           f"a sample — truncation loses meaning if the value is not "
                           f"just padded.",
        }
    if rule == "numeric":
        return {
            "ok": True, "rule_type": "REMOVE_SPECIAL_CHARS",
            "rule_config": {"keep": "-."},
            "description": f"{field}: strip non-numeric characters, keeping the sign "
                           f"and decimal point.",
        }
    if rule == "scale":
        dp = int(finding.get("scale") or 2)
        return {
            "ok": True, "rule_type": "NUMBER_FORMAT",
            "rule_config": {"decimals": dp},
            "description": f"{field}: round to {dp} decimal place(s), which is what "
                           f"Oracle would do on load.",
        }
    # do_not_populate
    return {
        "ok": True, "rule_type": "CONSTANT", "rule_config": {"value": ""},
        "description": f"{field}: ship blank — the template says this column is not "
                       f"used and no value should be provided.",
    }


def summarize(plans: list[dict]) -> dict:
    """What a bulk 'fix everything fixable' would and would not do."""
    fixable = [p for p in plans if p.get("ok")]
    skipped = [p for p in plans if not p.get("ok")]
    return {
        "fixable": len(fixable),
        "skipped": len(skipped),
        # Named, not just counted: "3 of 5 fixed" with no word on the other two reads
        # as though they were fine.
        "skipped_reasons": [{"rule": p.get("rule"), "reason": p.get("reason")}
                            for p in skipped],
    }
