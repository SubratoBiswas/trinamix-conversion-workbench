"""The nine authored Customer rules from CW_Issues rows 15-24.

These were the "Authorable" rows: the engine could express each one, but nobody had
typed it, because the source column spellings come from the analyst's extract and a
wrong guess ships silently in a file that looks correct. On 30-Jul the analyst said to
author all of them, so they are written here as data rather than typed one at a time
into the rule box — same destination, same precedence, just pre-filled.

TWO THINGS THAT MAKE THIS SAFE TO PRE-FILL
------------------------------------------
1. Every source column is a LIST of candidate spellings, not one guess. COALESCE skips
   a column that is absent or blank, and a CASE_WHEN branch on an absent column reads
   as blank and falls through — so naming six plausible spellings costs nothing and
   binds whichever one the extract actually uses. One guessed spelling binds to nothing
   and fails quietly, which is the failure mode worth engineering against.
2. Nothing overwrites a real source value. SEQUENCE keeps a source key when there is
   one; CONCAT falls back rather than emitting a bare separator; every CASE_WHEN
   default is either the stated fallback or blank, never an invented code.

SECTION 10.6 — read this before touching CW #23
-----------------------------------------------
Auto-generated key numbers were REMOVED once, on the analyst's instruction, because a
manufactured unique value makes genuine duplicates look distinct and they then load
twice. The 30-Jul instruction reverses that for Party Number. Two safeguards keep the
old problem from returning: the sequence is assigned at finalize, AFTER duplicate
decisions have dropped the rows that must not ship, and a SEQUENCE field must never be
added to the duplicate-identity columns — the natural key is what identifies a row.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_FILE = Path(__file__).resolve().parent.parent / "data" / "customer_rules_nextpower.json"
_NORM = re.compile(r"[^a-z0-9]+")

# Applied in this order, and the order is load-bearing: CW #23 (Party Number) reads
# Party Type, which CW #22 derives. A rule that depends on another rule's output has
# to run after it, so `sequence` is assigned from this list's index.
_ORDER = ("COALESCE", "CONCAT", "CASE_WHEN", "SUFFIX_WHEN", "SEQUENCE")


def _n(s: Any) -> str:
    return _NORM.sub("", str(s or "").lower())


def load_rules() -> list[dict]:
    """The authored rules, ordered so a dependent rule runs after its input."""
    try:
        doc = json.loads(_FILE.read_text(encoding="utf-8"))
    except Exception as exc:                                    # noqa: BLE001
        log.warning("customer_rules_nextpower.json unreadable: %s", exc)
        return []
    rules = [r for r in (doc.get("rules") or [])
             if r.get("target_field") and r.get("rule_type")]
    rules.sort(key=lambda r: (_ORDER.index(r["rule_type"])
                              if r["rule_type"] in _ORDER else len(_ORDER)))
    return rules


def open_questions() -> list[str]:
    """What the analyst still has to confirm. Surfaced, not buried in a comment."""
    try:
        return list(json.loads(_FILE.read_text(encoding="utf-8"))
                    .get("_open_questions") or [])
    except Exception:                                           # noqa: BLE001
        return []


def applies_to(target_object: Optional[str]) -> bool:
    return "customer" in str(target_object or "").lower()


async def apply_to_conversion(conversion, *, replace: bool = False) -> dict:
    """Create the rules on one Customer conversion. Idempotent.

    A field name repeats across the 19 Customer interface sheets (that is CW #11's
    whole problem), so a rule is bound to EVERY field with that name — the rule is a
    statement about the field, not about one sheet. Where that is wrong, the per-sheet
    scope from CW #12-14 narrows it.

    ``replace=False`` leaves an existing rule for the same field and type alone, so a
    hand-edit is never clobbered by a re-seed. ``replace=True`` re-applies the
    authored config, which is what "reset to the authored version" means.
    """
    from app.models.fbdi import FBDIField
    from app.models.transformation import TransformationRule

    if not applies_to(getattr(conversion, "target_object", None)):
        return {"applied": 0, "skipped": 0, "note": "not a Customer conversion"}
    if not getattr(conversion, "template_id", None):
        return {"applied": 0, "skipped": 0, "note": "conversion has no template"}

    fields = await FBDIField.find(
        FBDIField.template_id == conversion.template_id).to_list()
    by_norm: dict[str, list] = {}
    for f in fields:
        by_norm.setdefault(_n(f.field_name), []).append(f)

    applied = updated = skipped = unmatched = 0
    details: list[dict] = []
    for i, r in enumerate(load_rules()):
        targets = by_norm.get(_n(r["target_field"])) or []
        if not targets:
            unmatched += 1
            details.append({"cw": r.get("cw"), "target_field": r["target_field"],
                            "status": "no such field in this template"})
            continue
        for f in targets:
            existing = await TransformationRule.find_one({
                "conversion_id": conversion.id, "target_field_id": f.id,
                "rule_type": r["rule_type"],
            })
            if existing and not replace:
                skipped += 1
                continue
            if existing:
                await existing.set({"rule_config": r["rule_config"],
                                    "description": r.get("description"),
                                    "sequence": i})
                updated += 1
                continue
            await TransformationRule(
                conversion_id=conversion.id, target_field_id=f.id,
                rule_type=r["rule_type"], rule_config=r["rule_config"],
                description=(f"CW #{r.get('cw')} — {r.get('description') or ''}").strip(),
                sequence=i,
            ).insert()
            applied += 1
        details.append({"cw": r.get("cw"), "target_field": r["target_field"],
                        "rule_type": r["rule_type"], "sheets": len(targets),
                        "status": "applied"})
    return {
        "applied": applied, "updated": updated, "skipped": skipped,
        "unmatched": unmatched, "rules": details,
        "open_questions": open_questions(),
        "message": _message(applied, updated, skipped, unmatched),
    }


def _message(applied: int, updated: int, skipped: int, unmatched: int) -> str:
    bits = []
    if applied:
        bits.append(f"{applied} rule binding(s) created")
    if updated:
        bits.append(f"{updated} re-applied")
    if skipped:
        bits.append(f"{skipped} left as configured")
    if unmatched:
        # Named, not silent: a target field this template has no column for means the
        # rule will never fire, and "9 rules authored" would otherwise read as done.
        bits.append(f"{unmatched} rule(s) match no field in this template and will "
                    f"not fire")
    return "; ".join(bits) or "Nothing to apply."
