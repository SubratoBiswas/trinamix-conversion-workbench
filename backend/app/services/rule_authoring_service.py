"""Author a rule in plain English once, and learn it for several modules.

WHAT WAS MISSING
----------------
Plain-English translation already existed, but only inside the Rule Author modal,
which is opened FROM a target field — so a rule could not be written without
first picking a field, and the learning it produced was stamped with that one
conversion's ``target_object``. An Item rule therefore never reached BOM, even
though "trim the part number and upper-case it" is the same instruction in both.

This writes ONE learning PER selected module from a single sentence, all sharing
the translated rule definition, so a convention stated once governs every module
the analyst says it applies to.

SCOPE IS DELIBERATELY NARROW
----------------------------
Learnings are written client-scoped and stamped with the source system, matching
``learning_service``: a rule describing how THIS client's NetSuite extract is
shaped is not a fact about NetSuite generally, and must not leak to another
client or to a SyteLine conversion. The set of modules is explicit rather than
inferred — guessing that an Item rule also suits Customer is exactly the kind of
silent over-reach that makes a learning library untrustworthy.

The translation itself is delegated to ``rule_translation_service``; this module
only decides WHERE the result is stored, which is the part worth unit-testing
without a model.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

log = logging.getLogger(__name__)

# Objects a rule may be learned for. Kept explicit so a typo becomes an error
# rather than a learning filed under an object nothing will ever read.
KNOWN_OBJECTS = ("Supplier", "Customer", "Item", "BOM", "Employee",
                 "Subinventory", "Locator", "Price List", "Lot Number",
                 "Serial Number", "Item Category", "Item Cost",
                 "On Hand Balance", "Sales Order", "Purchase Order",
                 "Requisition", "Receipt")

_NORM = re.compile(r"[^a-z0-9]+")


def _n(s) -> str:
    return _NORM.sub("", str(s).lower()) if s is not None else ""


def normalize_objects(objects: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split requested modules into (recognised canonical names, unknown).

    Returns the catalogue's own spelling so learnings are keyed consistently —
    "bom", "BOM" and "Bill of Material" must not become three separate rows that
    each cover a third of the conversions.
    """
    by_norm = {_n(o): o for o in KNOWN_OBJECTS}
    by_norm.setdefault("billofmaterial", "BOM")
    by_norm.setdefault("billofmaterials", "BOM")
    by_norm.setdefault("bommaster", "BOM")
    by_norm.setdefault("itemmaster", "Item")
    by_norm.setdefault("vendor", "Supplier")
    ok, bad = [], []
    for o in objects or []:
        # None must be skipped explicitly: str(None) is "None", which is truthy
        # after strip and would be reported to the user as an unknown module
        # literally named "None".
        if o is None or not str(o).strip():
            continue
        canon = by_norm.get(_n(o))
        if canon:
            if canon not in ok:
                ok.append(canon)
        else:
            bad.append(str(o).strip())
    return ok, bad


def plan_learnings(*, translated: dict, target_field: str, objects: list[str],
                   description: str) -> list[dict]:
    """One learning payload per module, from a single translated rule.

    Pure: takes the translator's output and returns what would be written, so the
    fan-out can be tested without a model or a database.
    """
    rule_type = (translated or {}).get("rule_type")
    config = (translated or {}).get("config") or {}
    if not rule_type or not target_field or not objects:
        return []
    src = config.get("source_column") or (translated or {}).get("source_column")
    return [{
        "kind": "column_mapping" if src else "example_default",
        "target_object": obj,
        "target_field": target_field,
        "rule_type": rule_type,
        "rule_config": config,
        # The sentence the analyst actually wrote is kept verbatim. When this
        # rule surfaces on another module months later, "why does BOM do this?"
        # is answerable without reconstructing intent from a rule_type.
        "original_value": src or "(rule)",
        "resolved_value": target_field,
        "captured_from": f"plain-English rule: {description.strip()[:180]}",
    } for obj in objects]


async def author_rule_for_objects(
    conversion, description: str, *, target_field: str,
    objects: Iterable[str], captured_by: str = "",
    target_field_id: Optional[str] = None,
    source_column: Optional[str] = None,
) -> dict:
    """Translate the sentence, then learn it for every requested module."""
    from app.services.client_service import client_id_for_conversion
    from app.services.learning_service import _upsert, source_erp_for_conversion
    from app.services.rule_translation_service import translate_rule

    wanted, unknown = normalize_objects(objects)
    if not wanted:
        return {"written": 0, "objects": [], "unknown": unknown,
                "error": "No recognised module was selected."}

    translated = await translate_rule(
        conversion, description,
        target_field_id=target_field_id, source_column=source_column)

    plans = plan_learnings(translated=translated, target_field=target_field,
                           objects=wanted, description=description)
    if not plans:
        return {"written": 0, "objects": [], "unknown": unknown,
                "translated": translated,
                "error": "The description could not be turned into a rule."}

    client_id = await client_id_for_conversion(conversion)
    source_erp = await source_erp_for_conversion(conversion)
    written: list[str] = []
    for p in plans:
        try:
            lm = await _upsert(
                kind=p["kind"], category="Column Mapping Alias",
                original_value=p["original_value"], resolved_value=p["resolved_value"],
                target_object=p["target_object"], target_field=p["target_field"],
                rule_type=p["rule_type"], rule_config=p["rule_config"],
                project_id=getattr(conversion, "project_id", None),
                client_id=client_id, source_erp=source_erp,
                captured_from=p["captured_from"], captured_by=captured_by,
                # An analyst typing a rule IS an explicit action, so it may revive
                # a learning they previously retired — unlike auto-capture, which
                # must respect the tombstone.
                revive=True,
            )
            if lm is not None:
                written.append(p["target_object"])
        except Exception as exc:                                # noqa: BLE001
            log.warning("rule authoring: %s failed for %s: %s",
                        p["target_field"], p["target_object"], exc)
    return {
        "written": len(written),
        "objects": written,
        "unknown": unknown,
        "translated": translated,
        "source_erp": source_erp,
    }
