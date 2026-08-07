"""Seed the reusable Mapping Knowledge Base from the bundled metadata catalog.

`app/data/mapping_catalog.json` holds standard source→Oracle FBDI column mappings
assembled from public schemas (NetSuite, Infor SyteLine, Salesforce → Oracle
Fusion FBDI). On startup we upsert each row into the LearnedMapping registry as a
reusable ``column_mapping`` rule so the learning engine
(``apply_learned_to_conversion``) can auto-apply it whenever a matching source
file is converted in future.

Idempotent + non-destructive: an existing rule for the same
(target_object, target_field, source_field) is left untouched — this never
overwrites gold/prompt-captured learnings, only fills in catalog rows that
aren't there yet.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.models.learned import LearnedMapping
from app.services import mapping_store

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"
_CATALOG = _DATA / "mapping_catalog.json"
# Analyst-authored NextPower Item Field Mapping Document, distilled to the
# standard-field source→Oracle column mappings (the EFF rows are handled
# separately). Seeded the same way so an item file from any of the five source
# systems (Arena EBOS/Ratana Lee/Anaplan, SyteLine, NetSuite) auto-maps.
_ITEM_MAPPINGS = _DATA / "item_field_mappings.json"
# Analyst-authored NextPower Supplier Field Mapping doc (v3): NetSuite "SS
# Vendors" + Arena eBOS → the 6 Oracle supplier interface objects.
_SUPPLIER_MAPPINGS = _DATA / "supplier_field_mappings.json"
# Analyst-confirmed NextPower supplier TRANSFORM rows (from the Raman feedback /
# meeting): Delivery Method & Delivery Channel derived from the Email/Fax
# Transaction flags (CASE_WHEN), Phone/Fax split into Country/Area/Extension/Number
# (PHONE_PART), and Use Withholding Tax <- Default WT Code. Seeded as
# transformation-carrying column_mapping learnings across the 3 supplier objects.
_SUPPLIER_TRANSFORMS = _DATA / "supplier_transform_mappings.json"
# Analyst-authored NextPower Customer FBDI Field Mapping doc (V1): NetSuite
# customer export → the 19-sheet Oracle Fusion Customer Import (HZ_IMP/RA). The
# distinct source→target pairs (account/party keys + name/tax/email/phone/credit)
# propagate by field name across every interface sheet they appear in.
_CUSTOMER_MAPPINGS = _DATA / "customer_field_mappings.json"
# Analyst-authored NextPower Employee HDL Field Mapping doc (v3): Workday worker
# export → Oracle HCM via HDL. Only the true source-driven columns are seeded
# here (target_object "Employee HDL"); HDL constants, composite SourceSystemId
# keys and value maps are applied by the HDL generator from the loader schema.
_EMPLOYEE_HDL_MAPPINGS = _DATA / "employee_hdl_field_mappings.json"
# Analyst-authored NextPower BOM (Item Structure) FBDI mapping docs (Tracker +
# eBOS): Arena BOM extracts → the Item Structure import workbook
# (EGP_STRUCTURES_INTERFACE / EGP_COMPONENTS_INTERFACE). Source→target column
# rows PLUS the analyst-fixed constant defaults (Transaction Type=SYNC,
# Structure Name=Primary, Organization Code=NXT_ITEM_ORG).
_BOM_MAPPINGS = _DATA / "bom_field_mappings.json"
# Analyst DO-NOT-MAP list for NextPower Item: NetSuite source columns (the
# yellow-highlighted custom custitem_* attributes) that AI over-maps but the item
# mapping doc excludes. Seeded as ``ignore_source`` learnings so the mapper never
# uses them as a source for any Item field.
_ITEM_DONOTMAP = _DATA / "item_donotmap_columns.json"


async def _seed_catalog_file(path: Path, captured_from: str, *,
                             is_global: bool = False, client_id=None,
                             effective_date=None) -> dict:
    """Seed source→FBDI rows as reusable learnings. ``is_global`` marks
    client-agnostic public-schema rows (apply to every client); otherwise the rows
    are scoped to ``client_id`` (the analyst docs are one client's source data)."""
    if not path.exists():
        return {"seeded": 0, "skipped": 0, "note": f"{path.name} not found"}
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog seed: could not read %s: %s", path.name, exc)
        return {"seeded": 0, "skipped": 0, "error": str(exc)}

    seeded = updated = deduped = skipped = retired = superseded = 0
    for r in rows:
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        src_field = (r.get("source_field") or "").strip()
        if not (tgt_obj and tgt_field and src_field):
            continue
        # A later analyst document can RE-POINT a target field at a different source
        # column. Seeding the new pair alone leaves the old one in place, so the
        # target ends up with two competing column_mappings and which one reaches
        # the FBDI depends on the per-target dedup tie-break (QA issue #6). Rows may
        # therefore declare the source(s) they supersede; those learnings are
        # retired here. User-deleted rows are left alone — they are already retired,
        # and resurrecting one just to delete it again would clear the tombstone.
        for old_src in (r.get("replaces_source_field") or []):
            if str(old_src).strip() == src_field:
                continue
            for prior in await LearnedMapping.find(
                LearnedMapping.kind == "column_mapping",
                LearnedMapping.target_object == tgt_obj,
                LearnedMapping.target_field == tgt_field,
                LearnedMapping.original_value == str(old_src).strip(),
            ).to_list():
                await prior.delete()
                superseded += 1

        rule_type = r.get("rule_type")
        rule_config = (r.get("rule_config") if rule_type else {
            "source_column": src_field, "source_label": r.get("source_label"),
            "fbdi_sheet": r.get("fbdi_sheet"), "confidence": r.get("confidence"),
        })
        # One more dated entry. The upsert-with-upgrade and de-dupe dance that
        # used to live here existed because one decision could be stored under
        # several object spellings and several scopes at once and they then
        # competed. There is one key now, so there is one row to update, and the
        # store already refuses to resurrect a decision the analyst retired or to
        # walk a later one backwards.
        _cid = None if is_global else client_id
        _before = await mapping_store.find_rows_for_key(
            target_field=tgt_field, client_id=_cid,
            source_erp=r.get("source_system"))
        _existed = any(getattr(x, "kind", None) == "column_mapping"
                       and not getattr(x, "is_deleted", False) for x in _before)
        row = await mapping_store.record_learning(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=src_field, resolved_value=tgt_field,
            target_object=tgt_obj, target_field=tgt_field,
            client_id=_cid, rule_type=rule_type, rule_config=rule_config,
            source_erp=r.get("source_system"), captured_from=captured_from,
            captured_by=None, effective_date=effective_date,
            undated=effective_date is None,
        )
        if row is None:
            retired += 1
            continue
        if _existed:
            updated += 1
            continue
        seeded += 1

    if seeded or updated or deduped or skipped or retired or superseded:
        logger.info("%s: seeded %d, upgraded %d, de-duped %d, skipped %d, "
                    "retired-respected %d, superseded %d (of %d rows)",
                    path.name, seeded, updated, deduped, skipped, retired,
                    superseded, len(rows))
    return {"seeded": seeded, "updated": updated, "deduped": deduped,
            "skipped": skipped, "retired": retired, "superseded": superseded,
            "total": len(rows)}


async def _nextpower_client_id():
    """Resolve the bootstrap NextPower client id (the analyst docs are its data)."""
    from app.services.client_service import ensure_default_client
    c = await ensure_default_client()
    return c.id


async def seed_mapping_catalog() -> dict:
    """Standard public-schema source→FBDI catalog — GLOBAL (applies to every client)."""
    return await _seed_catalog_file(_CATALOG, "metadata catalog", is_global=True)


async def seed_item_field_mappings() -> dict:
    """Analyst-confirmed NextPower Item standard-field mappings (all 5 sources)."""
    return await _seed_catalog_file(_ITEM_MAPPINGS, "NXT item field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_supplier_field_mappings() -> dict:
    """Analyst-confirmed NextPower Supplier mappings (NetSuite SS Vendors + eBOS)
    across the 6 supplier interface objects, incl. the Business Relationship
    value-map. GLOBAL: supplier source→FBDI mapping is source-system knowledge
    (NetSuite / eBOS conventions), reusable for every client and future project."""
    return await _seed_catalog_file(_SUPPLIER_MAPPINGS, "NXT supplier field mapping doc",
                                    is_global=True)


async def seed_supplier_transform_mappings() -> dict:
    """Analyst-confirmed supplier transforms: Delivery Method/Channel derivation
    (CASE_WHEN on Email/Fax Transaction flags), Phone/Fax split (PHONE_PART),
    Use Withholding Tax <- Default WT Code, and the eBOS direct 1:1 rows — GLOBAL
    so every client / future project gets them for both source vocabularies."""
    return await _seed_catalog_file(_SUPPLIER_TRANSFORMS, "NXT supplier transform rules",
                                    is_global=True)


async def seed_customer_field_mappings() -> dict:
    """Analyst-confirmed NextPower Customer mappings (NetSuite → Fusion Customer
    Import). Key references (entitynumber/entityid) plus name/tax/email/phone/
    credit; propagate by field name across the 19 HZ_IMP/RA interface sheets."""
    return await _seed_catalog_file(_CUSTOMER_MAPPINGS, "NXT customer field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_employee_hdl_field_mappings() -> dict:
    """Analyst-confirmed NextPower Employee HDL mappings (Workday → Oracle HCM).
    Source-driven worker columns only; HDL constants, SourceSystemId keys and
    value maps are filled by the HDL generator from the loader schema."""
    return await _seed_catalog_file(_EMPLOYEE_HDL_MAPPINGS, "NXT employee HDL field mapping doc",
                                    client_id=await _nextpower_client_id())


async def seed_item_donotmap_columns() -> dict:
    """Seed the NextPower Item DO-NOT-MAP source columns as ``ignore_source``
    learnings (client-scoped to NextPower, target_object 'Item'). The mapping
    engine drops any Item mapping whose source column is on this list, so AI can't
    over-populate fields from source columns the analyst deliberately excluded
    (the yellow-highlighted NetSuite custitem_* attributes). Idempotent."""
    import json as _json
    if not _ITEM_DONOTMAP.exists():
        return {"seeded": 0, "note": "item_donotmap_columns.json not found"}
    try:
        doc = _json.loads(_ITEM_DONOTMAP.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    obj = (doc.get("target_object") or "Item").strip()
    cols = [c for c in (doc.get("columns") or []) if isinstance(c, str) and c.strip()]
    nid = await _nextpower_client_id()
    seeded = kept = 0
    for col in cols:
        col = col.strip()
        row = await mapping_store.record_source_exclusion(
            target_object=obj, source_column=col,
            captured_from="NXT item over-map feedback (NetSuite do-not-map list)",
            client_id=nid, source_erp=doc.get("source_system"),
        )
        if row is None:
            kept += 1          # retired by the analyst — do not resurrect
            continue
        seeded += 1
    logger.info("item do-not-map: seeded %d, kept %d (of %d cols)", seeded, kept, len(cols))
    return {"seeded": seeded, "kept": kept, "total": len(cols), "target_object": obj}


_SUPPLIER_DEFAULTS = _DATA / "supplier_default_values.json"


async def seed_supplier_default_values() -> dict:
    """Analyst-approved constant defaults for NextPower supplier objects (e.g.
    Supplier Site Invoice Match Option = 'Receipt'), seeded as ``example_default``
    learnings scoped to the NextPower client so future NextPower supplier
    conversions populate them automatically. Idempotent."""
    import json as _json
    if not _SUPPLIER_DEFAULTS.exists():
        return {"seeded": 0, "note": "supplier_default_values.json not found"}
    try:
        rows = _json.loads(_SUPPLIER_DEFAULTS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    seeded = kept = 0
    for r in rows:
        const = r.get("constant")
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if const is None or not (tgt_obj and tgt_field):
            continue
        row = await mapping_store.record_learning(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field,
            client_id=nid, captured_from="NXT supplier analyst default",
            captured_by=None, undated=True,
        )
        if row is None:
            kept += 1          # retired by the analyst — do not resurrect
            continue
        seeded += 1
    logger.info("supplier default values: seeded %d, kept %d", seeded, kept)
    return {"seeded": seeded, "kept": kept, "total": len(rows)}


async def seed_bom_field_mappings() -> dict:
    """Analyst-confirmed NextPower BOM (Item Structure) mappings, from the Tracker
    + eBOS BOM FBDI docs. Two parts, both scoped to the NextPower client:

    * source→target COLUMN rows (Item/Component/Structure item names, Item
      Sequence, Quantity) for BOTH Arena source vocabularies — seeded via the
      shared catalog upsert so a BOM file from either source auto-maps; and
    * the analyst-fixed CONSTANT defaults on EGP_STRUCTURES_INTERFACE
      (Transaction Type=SYNC, Structure Name=Primary, Organization Code=
      NXT_ITEM_ORG) — seeded as ``example_default`` learnings (rows carry a
      ``constant`` instead of a ``source_field``, which the column seeder skips).
    Idempotent: constants are find-or-upgraded, never duplicated.
    """
    import json as _json
    nid = await _nextpower_client_id()
    # (1) source→target column mappings (rows with a source_field)
    res = await _seed_catalog_file(_BOM_MAPPINGS, "NXT BOM field mapping doc", client_id=nid)
    # (2) constant defaults (rows with a "constant" and no source_field)
    const_seeded = const_kept = 0
    try:
        rows = _json.loads(_BOM_MAPPINGS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        const = r.get("constant")
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if const is None or not (tgt_obj and tgt_field):
            continue
        row = await mapping_store.record_learning(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field,
            client_id=nid, captured_from="NXT BOM field mapping doc",
            captured_by=None, undated=True,
        )
        if row is None:
            const_kept += 1    # retired by the analyst — do not resurrect
            continue
        const_seeded += 1
    # BOM-02: retire the old Organization Code = NXT_ITEM_ORG constant. The input file
    # carries the real org code (e.g. IMO) and the constant was overwriting it on every
    # sheet. Removing the row from the JSON stops it being RE-seeded, but the learning
    # already written has to be tombstoned or it keeps applying. Idempotent — once
    # retired it stays retired, and it is scoped to this client + this field so nothing
    # else is touched. A passthrough (Organization Code <- "Organization Code") is
    # seeded above, so the column is not left blank.
    org_retired = 0
    try:
        from datetime import datetime as _dt
        from app.models.learned import LearnedMapping as _LM
        _q = {"kind": "example_default", "target_object": "BOM",
              "resolved_value": "NXT_ITEM_ORG"}
        if nid is not None:
            _q["client_id"] = nid
        for _lm in await _LM.find(_q, include_deleted=True).to_list():
            if getattr(_lm, "is_deleted", False):
                continue
            if mapping_store.normalise_field(_lm.target_field or "") != \
                    mapping_store.normalise_field("Organization Code"):
                continue
            await _lm.set({"is_deleted": True, "deleted_at": _dt.utcnow(),
                           "deleted_by": "bom-02-org-code-passthrough"})
            org_retired += 1
    except Exception as _exc:  # noqa: BLE001 — retirement is best-effort
        res["org_constant_retire_error"] = f"{type(_exc).__name__}: {_exc}"[:200]
    res["constants_seeded"] = const_seeded
    res["constants_kept"] = const_kept
    res["org_constant_retired"] = org_retired
    return res


from datetime import datetime as _dt


def _effective_date_of(doc: dict) -> "_dt | None":
    """When the instruction in this file was GIVEN — date AND, if stated, time.

    Stamped onto every learning the file seeds, because captured_at is the moment
    the row was written and every startup seed stamps utcnow — so on a redeploy the
    13-Jul strategy would look newer than the 30-Jul corrections and "latest wins"
    would invert itself on a restart.

    Precedence is resolved on the full timestamp everywhere else in the store (a UI
    edit and an auto-capture each carry a real time, and the resolver compares
    datetimes, not dates). Documents were the one exception — pinned to midnight — so
    a document lost to any same-day change made later in the day. Now the date string
    may carry a time ("2026-08-06T14:30", "2026-08-06 14:30:00") and it is honoured,
    so when there are several changes on one day the latest genuinely wins. A plain
    date still parses to midnight, exactly as before, so nothing existing shifts.
    """
    raw = str((doc or {}).get("_effective_date") or "").strip()
    if not raw:
        return None
    try:
        # fromisoformat handles a plain date (-> midnight) and a full timestamp.
        return _dt.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        pass
    try:
        return _dt.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return None


_SUPPLIER_CORRECTIONS = _DATA / "supplier_corrections_30jul.json"


async def seed_supplier_corrections_30jul() -> dict:
    """Seed the analyst's 30-Jul Supplier corrections as client-scoped learnings.

    Three shapes, each mapped to the learning kind that already carries it:
      blank    -> suppress_field   (nothing may fill it, including control defaults)
      constant -> example_default
      rule     -> column_mapping carrying the rule_type + config

    Seeded LAST of the supplier seeds so these values win a clash — they are the most
    recent analyst instruction. A mapping a person has since edited and approved in the
    UI still outranks all of it; apply_learned_to_conversion refuses to overwrite
    anything not approved by the engine itself.

    Idempotent, and it honours tombstones: a correction the analyst later retires is
    not resurrected on the next restart.
    """
    import json as _json
    if not _SUPPLIER_CORRECTIONS.exists():
        return {"seeded": 0, "note": "supplier_corrections_30jul.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_CORRECTIONS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    src = "analyst corrections 30-Jul-2026"
    seeded = updated = retired = 0
    for r in doc.get("rules", []):
        obj, fld = (r.get("target_object") or "").strip(), (r.get("target_field") or "").strip()
        action = (r.get("action") or "").strip()
        if not (obj and fld and action):
            continue
        kind = {"blank": "suppress_field", "constant": "example_default",
                "rule": "column_mapping"}.get(action)
        if not kind:
            continue
        resolved = ("" if action == "blank"
                    else str(r.get("value", "")) if action == "constant" else fld)
        # Identity includes captured_from for a RULE correction. A field like
        # Alternate Name legitimately carries several column_mapping rows — one per
        # source column — so keying only on (kind, object, field, client) matched an
        # arbitrary alias and updated THAT instead of creating the correction. Live,
        # two of the twelve corrections went missing exactly this way.
        _q = [LearnedMapping.kind == kind,
              LearnedMapping.target_object == obj,
              LearnedMapping.target_field == fld,
              LearnedMapping.client_id == nid]
        if kind == "column_mapping":
            _q.append(LearnedMapping.captured_from == src)
        row = await mapping_store.record_learning(
            kind=kind,
            category=("Left blank on purpose" if action == "blank"
                      else "Default Value" if action == "constant"
                      else "Column Mapping Alias"),
            original_value=("(blank)" if action == "blank"
                            else "(constant)" if action == "constant" else fld),
            resolved_value=resolved,
            target_object=obj, target_field=fld, client_id=nid,
            rule_type=r.get("rule_type") or ("suppress" if action == "blank" else None),
            rule_config=r.get("rule_config") or {"note": r.get("note", "")},
            captured_from=src, captured_by=None,
            effective_date=_effective_date_of(doc),
        )
        if row is None:
            retired += 1
            continue
        seeded += 1

    # A blank correction has to CHANGE the library and the mappings, not just be
    # added alongside them. Seeding the suppression while leaving the old
    # column_mapping alive left two rows saying opposite things about one field,
    # and the older one kept winning — Supplier Name New shipped the supplier name
    # on all 3,872 rows, Procurement BU "Nextracker Consolidated" on 5,315,
    # Liability Distribution an account string on 1,528. Analyst, 30-Jul: "modify
    # the learning, the code should change the learning and mapping as I am saying
    # now".
    from app.services.learning_service import enforce_blank_corrections
    pairs: list[tuple] = []
    for r in doc.get("rules", []):
        if (r.get("action") or "").strip() != "blank":
            continue
        fld = (r.get("target_field") or "").strip()
        objs = [(r.get("target_object") or "").strip()]
        if r.get("applies_to_all_sheets"):
            objs = await _sheet_objects_for(objs[0])
        pairs += [(o, fld) for o in objs if o]
    enforced: list[dict] = []
    scanned = 0
    try:
        # ONE pass for all of them. Per-field, this re-walked every conversion for
        # each of six blanks across six sheet objects — 36 sweeps of 232
        # conversions, two database round-trips each — and the on-demand reseed
        # never returned at all.
        _res = await enforce_blank_corrections(
            pairs, client_id=nid, captured_from=src,
            captured_by="analyst-correction-30jul",
            # The date of the instruction, so an approval a person made BEFORE it
            # does not outrank it. "For conflicts always the latest one should be
            # taken" (analyst, 30-Jul).
            as_of=_effective_date_of(doc))
        scanned = _res["conversions_scanned"]
        enforced = [f for f in _res["fields"]
                    if f["learnings_retired"] or f["mappings_blanked"]
                    or f["skipped_human"]]
    except Exception:  # noqa: BLE001 — never block startup on the sweep
        logger.exception("enforcing the 30-Jul blank corrections failed")
    if enforced:
        logger.info("30-Jul blank corrections enforced over %d conversions: %s",
                    scanned, enforced)

    # Rule corrections reach the conversions that already exist too. Seeding the
    # learning covers NEW conversions and the write-time overlay covers the file,
    # but the mapping rows of existing conversions kept whatever the matcher had
    # guessed — so the analyst opened one, saw the old derivation on screen, and had
    # no reason to believe the file said anything different.
    from app.services.learning_service import apply_rule_corrections
    rule_rows = []
    for r in doc.get("rules", []):
        if (r.get("action") or "").strip() != "rule" or not r.get("rule_type"):
            continue
        fld = (r.get("target_field") or "").strip()
        objs = [(r.get("target_object") or "").strip()]
        if r.get("applies_to_all_sheets"):
            objs = await _sheet_objects_for(objs[0])
        rule_rows += [(o, fld, r["rule_type"], r.get("rule_config") or {})
                      for o in objs if o]
    rules_applied: list[dict] = []
    try:
        _rr = await apply_rule_corrections(
            rule_rows, client_id=nid, captured_from=src,
            as_of=_effective_date_of(doc))
        rules_applied = [f for f in _rr["fields"]
                         if f["mappings_updated"] or f["skipped_human"]]
    except Exception:  # noqa: BLE001 — never block startup on the sweep
        logger.exception("applying the 30-Jul rule corrections failed")
    if rules_applied:
        logger.info("30-Jul rule corrections applied: %s", rules_applied)

    return {"seeded": seeded, "updated": updated, "left_retired": retired,
            "blank_enforcement": enforced,
            "rule_corrections": rules_applied,
            "protected_values": doc.get("protected_values", []),
            "open_questions": doc.get("_open_questions", [])}


def _obj_norm(s) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", str(s or "").lower())


async def _sheet_objects_for(base_object: str) -> list[str]:
    """Every seeded business object in this bundle — "Supplier" -> the six supplier
    sheets. Read from the templates rather than hard-coded, so a sheet added later
    inherits an analyst's "blank on ALL sheets" without anyone remembering to
    extend a list in here. Prefix-matched for the same reason the overlay is:
    Customer has a Batch ID column too, and the instruction was about suppliers.
    """
    from app.models.fbdi import FBDITemplate
    base = _obj_norm(base_object)
    if not base:
        return []
    objs = {base_object}
    for t in await FBDITemplate.find_all().to_list():
        bo = (getattr(t, "business_object", None) or "").strip()
        if bo and _obj_norm(bo).startswith(base):
            objs.add(bo)
    return sorted(objs)


_HCM_SOURCE_MAP = _DATA / "hcm_source_mapping.json"


async def seed_hcm_source_mapping() -> dict:
    """Seed the GREEN rows of the NXT HCM Field Mapping workbook.

    Green is the workbook's own legend for "Mapped"; Yellow is a question to
    NextPower, Orange a duplicate, Blue Oracle-required-but-missing, and Red
    "Not to Bring". Only green is imported, and it is read by CELL FILL rather
    than by eye — 20 of the 47 rows, every one of which also reads
    "Bring to Oracle = Yes".

    Two shapes beyond a plain column mapping, both taken from the workbook:

      * ONE source column feeding SEVERAL targets. "Country" maps to Country AND
        LegislationCode, so it becomes two learnings rather than one.
      * A CONSTANT written where a source column would go — "default value
        ( 1/1/1900 )" against EffectiveStartDate. That is a default, not a
        mapping, so it is seeded as example_default and never as a column.

    Scoped per HDL object through ``sheets``: the Location and Job fields must not
    leak onto Worker just because the field name matches, which is the same
    name-collision problem the Customer per-sheet scope exists to solve.

    Idempotent, client- and source-scoped, tombstone-respecting.
    """
    import json as _json
    if not _HCM_SOURCE_MAP.exists():
        return {"seeded": 0, "note": "hcm_source_mapping.json not found"}
    try:
        doc = _json.loads(_HCM_SOURCE_MAP.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    src = doc.get("_source") or "NXT HCM Field Mapping (green rows)"
    OBJ = "Employee HDL"          # the object the existing HDL learnings already use
    seeded = updated = retired = 0

    async def _put(kind, field, original, resolved, comps, extra):
        nonlocal seeded, updated, retired
        _before = await mapping_store.find_rows_for_key(
            target_field=field, client_id=nid, source_erp="workday")
        _existed = any(getattr(x, "kind", None) == kind
                       and not getattr(x, "is_deleted", False) for x in _before)
        row = await mapping_store.record_learning(
            kind=kind,
            category=("Column Mapping Alias" if kind == "column_mapping"
                      else "Default Value"),
            original_value=original, resolved_value=resolved,
            target_object=OBJ, target_field=field, client_id=nid,
            rule_config=extra, sheets=list(comps or []),
            source_erp="workday", captured_from=src, captured_by=None,
            effective_date=eff,
        )
        if row is None:
            retired += 1
            return
        if _existed:
            updated += 1
            return
        seeded += 1

    # A green row whose Oracle field exists on no HDL component is recorded for the
    # analyst but never seeded as a live mapping — the workbook's own comment on
    # Cost Center is "No suitable field is available in Oracle for this information",
    # and a learning pointing at a phantom field reads as done and does nothing.
    no_field = 0
    for m in doc.get("mappings", []):
        col = (m.get("source_column") or "").strip()
        fld = (m.get("target_field") or "").strip()
        if not (col and fld):
            continue
        if m.get("oracle_field_exists") is False:
            no_field += 1
            continue
        await _put("column_mapping", fld, col, fld, m.get("hdl_components"),
                   {"hdl_object": m.get("hdl_object"),
                    "hdl_components": m.get("hdl_components") or [],
                    "hdl_block": m.get("hdl_block"),
                    "as_written_in_workbook": m.get("as_written_in_workbook"),
                    "as_written_in_workbook_target": m.get("as_written_in_workbook_target"),
                    "verified_against_extract": m.get("verified_against_extract"),
                    "note": m.get("note") or "", "source_row": m.get("source_row")})

    for c in doc.get("constants", []):
        fld = (c.get("target_field") or "").strip()
        if not fld:
            continue
        # EffectiveStartDate lives on NINE components. The original_value carries the
        # component so Location's 1900 default and Job's are two rows, not one row
        # overwriting the other.
        comps = c.get("hdl_components") or []
        tag = f"(constant:{comps[0]})" if comps else "(constant)"
        await _put("example_default", fld, tag, c.get("value") or "", comps,
                   {"hdl_object": c.get("hdl_object"),
                    "hdl_components": comps,
                    "note": c.get("note") or "", "source_row": c.get("source_row")})

    logger.info("HCM green mappings: %d seeded, %d updated, %d left retired, "
                "%d with no Oracle field", seeded, updated, retired, no_field)
    return {"seeded": seeded, "updated": updated, "left_retired": retired,
            "no_oracle_field": no_field,
            "mappings": len(doc.get("mappings", [])),
            "constants": len(doc.get("constants", []))}


_CUSTOMER_SHEET_SCOPE = _DATA / "customer_sheet_scope.json"


async def seed_customer_sheet_scope() -> dict:
    """Seed the Customer per-sheet mapping scope (CW_Issues 2, rows 13-15 and 26).

    The mechanism for this shipped a while ago — LearnedMapping.sheets /
    exclude_sheets, honoured by learning_service.sheet_allowed — and NOT ONE seeded
    learning in the whole catalog used it. Shipped and inert: an instruction reading
    "map id to Party Original System Reference in all sheets EXCEPT
    HZ_IMP_CLASSIFICS_T" was applied to all sheets, the named one included, because
    nothing ever wrote the exclusion down.

    Idempotent, client-scoped, tombstone-respecting, and it UPDATES the scope on an
    existing row rather than inserting a duplicate — two learnings for one
    source/target pair differing only in scope is a coin toss, which is the shape of
    bug this file exists to close.
    """
    import json as _json
    if not _CUSTOMER_SHEET_SCOPE.exists():
        return {"seeded": 0, "note": "customer_sheet_scope.json not found"}
    try:
        doc = _json.loads(_CUSTOMER_SHEET_SCOPE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    src = doc.get("_source") or "customer sheet scope"
    seeded = updated = retired = 0

    for m in doc.get("mappings", []):
        col, tgt = (m.get("source_column") or "").strip(), (m.get("target_field") or "").strip()
        if not (col and tgt):
            continue
        scope = {"sheets": list(m.get("sheets") or []),
                 "exclude_sheets": list(m.get("exclude_sheets") or [])}
        row = await mapping_store.record_learning(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=col, resolved_value=tgt,
            target_object="Customer", target_field=tgt, client_id=nid,
            rule_config={"issue": m.get("issue"), "note": m.get("note") or ""},
            captured_from=src, captured_by=None, effective_date=eff, **scope,
        )
        if row is None:
            retired += 1
            continue
        seeded += 1

    sup_seeded = sup_updated = 0
    for sp in doc.get("suppressions", []):
        tgt = (sp.get("target_field") or "").strip()
        if not tgt:
            continue
        scope = {"sheets": list(sp.get("sheets") or []),
                 "exclude_sheets": list(sp.get("exclude_sheets") or [])}
        row = await mapping_store.record_learning(
            kind="suppress_field", category="Left blank on purpose",
            original_value="(blank)", resolved_value="",
            target_object="Customer", target_field=tgt, client_id=nid,
            rule_type="suppress",
            rule_config={"issue": sp.get("issue"), "note": sp.get("note") or ""},
            captured_from=src, captured_by=None, effective_date=eff, **scope,
        )
        if row is None:
            retired += 1
            continue
        sup_seeded += 1

    logger.info("customer sheet scope: %d mappings seeded, %d updated; "
                "%d suppressions seeded, %d updated; %d left retired",
                seeded, updated, sup_seeded, sup_updated, retired)
    return {"seeded": seeded, "updated": updated, "suppressions_seeded": sup_seeded,
            "suppressions_updated": sup_updated, "left_retired": retired}


_SUPPLIER_SOURCE_MAP_30JUL = _DATA / "supplier_source_mapping_30jul.json"
_SUPPLIER_SOURCE_MAP_31JUL = _DATA / "supplier_source_mapping_31jul.json"
# NEWEST FIRST. "The last mapping with respect to date is final" (analyst, 31-Jul), so
# the edition the seeder reads is the latest one shipped, and the older file stays on
# disk only as the fallback for an environment that has not taken the new data yet.
_SUPPLIER_SOURCE_MAP_EDITIONS = [_SUPPLIER_SOURCE_MAP_31JUL, _SUPPLIER_SOURCE_MAP_30JUL]
_SUPPLIER_SOURCE_MAP = _SUPPLIER_SOURCE_MAP_30JUL      # kept for older imports


async def seed_supplier_source_mapping() -> dict:
    """Seed the GREEN rows of the NXT Supplier Mapping workbook as client-scoped,
    source-system-scoped column mappings.

    Green is the workbook's own legend for "Mapped". The other colours mean
    Questions to NextPower, Duplicate, Oracle-required-but-missing and Not-to-bring —
    none of which is an instruction to map something, so none is imported. Rows whose
    Oracle field is "DFF" or "standard" are excluded too: a descriptive flexfield is a
    decision about where a value belongs, not a mapping the engine can apply.

    THE SOURCE COLUMN IS THE PHYSICAL ONE. Debayon Mallik, 31-Jul: "for mapping we must
    consider the Source Table Column name (the last column of the mapping file) as the
    source columns." That column holds the name the extract actually has
    (``federal_reportable``, ``legal_name``); "Source Column Name" holds the NetSuite
    UI label ("1099 Eligible", "Legal Name"). Binding on the label put five named
    Oracle fields — Federal reportable, Tax Registration Number, Prefix, Taxpayer ID
    and B2B Supplier Site Code — onto columns the file does not contain, which reads as
    mapped on screen and ships an empty column. SyteLine rows have no technical column
    and never needed one: there the label already IS the physical name (``vend_num``,
    ``addr##1``). Extraction is in the data file; this function only loads it.

    A NEW EDITION SUPERSEDES THE OLD ONE IN PLACE. The 31-Jul workbook rebinds seven
    fields that the 30-Jul workbook had bound differently — including swapping Supplier
    Name and Alternate Name — and a plain find-or-insert would leave BOTH live, which
    is the forking problem that makes "the latest instruction wins" unexpressible: two
    rows for one field cannot say which is current. Rows written by the superseded
    edition are therefore rewritten rather than duplicated, and only those: a learning
    from any other source, and one the analyst retired, is left exactly as it is.

    Keyed by source system, so NetSuite's mapping for a field and SyteLine's are two
    rows. Idempotent, and it honours tombstones like every other seeder.
    """
    import json as _json
    path = next((p for p in _SUPPLIER_SOURCE_MAP_EDITIONS if p.exists()), None)
    if path is None:
        return {"seeded": 0, "note": "no supplier_source_mapping_*.json found"}
    try:
        doc = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    _eff = _effective_date_of(doc)
    # The label is the row's IDENTITY across editions — the next edition finds its
    # predecessor's rows by this exact string — so it is carried in the data file
    # rather than derived from the filename, which would change under a rename.
    src_label = (doc.get("_label")
                 or f"NXT Supplier Mapping ({path.stem.rsplit('_', 1)[-1]}, green rows)")
    superseded = (doc.get("_supersedes") or "").strip()
    seeded = kept = retired = rebound = 0
    for m in doc.get("mappings", []):
        tgt, col = (m.get("target_field") or "").strip(), (m.get("source_column") or "").strip()
        if not (tgt and col):
            continue
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == "column_mapping",
            LearnedMapping.target_object == "Supplier",
            LearnedMapping.target_field == tgt,
            LearnedMapping.original_value == col,
            LearnedMapping.source_erp == m.get("source_erp"),
            include_deleted=True,
        )
        if existing and getattr(existing, "is_deleted", False):
            retired += 1
            continue
        if existing:
            kept += 1
            continue
        # No row for THIS column — but the superseded edition may hold a row for the
        # same (field, system) pointing somewhere else. Rewrite that one instead of
        # adding a second, so the field keeps exactly one answer.
        prior = None
        if superseded:
            prior = await LearnedMapping.find_one(
                LearnedMapping.kind == "column_mapping",
                LearnedMapping.target_object == "Supplier",
                LearnedMapping.target_field == tgt,
                LearnedMapping.source_erp == m.get("source_erp"),
                LearnedMapping.captured_from == superseded,
            )
        row = await mapping_store.record_learning(
            kind="column_mapping", category="Column Mapping Alias",
            original_value=col, resolved_value=tgt,
            target_object="Supplier", target_field=tgt, client_id=nid,
            rule_config={"oracle_page": m.get("sheet") or "",
                         "note": m.get("note") or "",
                         "workbook_column_name": m.get("workbook_column_name") or "",
                         "bound_by": m.get("bound_by") or ""},
            source_erp=m.get("source_erp"), captured_from=src_label,
            captured_by=None, effective_date=_eff,
        )
        if row is None:
            retired += 1
            continue
        if prior is not None:
            rebound += 1
            continue
        seeded += 1
    return {"edition": path.name, "seeded": seeded, "rebound_from_previous_edition": rebound,
            "already_present": kept, "left_retired": retired,
            "excluded_with_reason": doc.get("_excluded_with_reason", []),
            "open_questions": doc.get("_open_questions", [])}


_SUPPLIER_STRATEGY = _DATA / "supplier_strategy_defaults.json"


def _eff_strategy(doc: dict):
    """The date the strategy document itself carries.

    Without it these rows would be stamped with the boot time and the 13-Jul
    strategy would out-rank the 30-Jul corrections after every redeploy — which
    is the precedence inversion this whole change exists to end.
    """
    return _effective_date_of(doc)


async def seed_supplier_strategy_defaults() -> dict:
    """Seed the NextPower Supplier Conversion Strategy (v1.0, section 7) as
    client-scoped ``example_default`` learnings.

    The strategy is a SIGNED functional specification, so it is the governing
    authority for this conversion: seeding it as learnings puts it ahead of the
    deterministic control constants and ahead of AI in the mapping precedence.
    Two rules are deliberately NOT seeded (see ``open_items`` in the JSON): the
    Procurement BU crosswalk does not exist yet, and the PROSPECTIVE branch of
    Business Relationship needs its source column confirmed — guessing either
    would put wrong values into a load file that looks correct. Idempotent, and
    it respects tombstones like every other seeder.
    """
    import json as _json
    if not _SUPPLIER_STRATEGY.exists():
        return {"seeded": 0, "note": "supplier_strategy_defaults.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_STRATEGY.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}
    nid = await _nextpower_client_id()
    src = doc.get("_source", "NextPower Supplier Conversion Strategy")
    seeded = kept = retired = derived = 0
    for r in doc.get("rules", []):
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if not (tgt_obj and tgt_field):
            continue
        const = r.get("constant")
        if const is None:
            derived += 1        # derive rules (city / BU-city) are mappings, not constants
            continue
        row = await mapping_store.record_learning(
            kind="example_default", category="Default Value",
            original_value="(constant)", resolved_value=str(const),
            target_object=tgt_obj, target_field=tgt_field, client_id=nid,
            rule_config={"condition": r.get("condition", ""),
                         "fill_blank_only": bool(r.get("fill_blank_only"))},
            captured_from=src, captured_by=None, effective_date=_eff_strategy(doc),
        )
        if row is None:
            retired += 1
            continue
        seeded += 1
    # Analyst refinements captured in live review: blank-on-purpose fields and
    # row-aware rules (e.g. Alternate Name blanked when it duplicates Supplier
    # Name). Seeded as suppress_field / column_mapping-with-rule learnings.
    a_sup = a_rule = a_skip = 0
    for r in (doc.get("analyst_rules", {}) or {}).get("rules", []):
        tgt_obj = (r.get("target_object") or "").strip()
        tgt_field = (r.get("target_field") or "").strip()
        if not (tgt_obj and tgt_field):
            continue
        if r.get("suppress"):
            kind, cat, orig, res, rtype, rcfg = ("suppress_field", "Suppressed (analyst rule)",
                                                 "(blank)", "", "suppress", {})
        elif r.get("rule_type") == "BLANK_IF_EQUALS":
            kind, cat = "rule", "Transformation Rule"
            orig = (r.get("rule_config") or {}).get("other_column", "")
            res, rtype, rcfg = tgt_field, "BLANK_IF_EQUALS", r.get("rule_config") or {}
        else:
            a_skip += 1        # constants already covered above, or not implementable yet
            continue
        row = await mapping_store.record_learning(
            kind=kind, category=cat, original_value=orig, resolved_value=res,
            target_object=tgt_obj, target_field=tgt_field,
            rule_type=rtype, rule_config=rcfg, client_id=nid,
            captured_from=(doc.get("analyst_rules", {}) or {}).get("_source", src),
            captured_by=None, effective_date=_eff_strategy(doc),
        )
        if row is None:
            retired += 1
            continue
        if kind == "suppress_field":
            a_sup += 1
        else:
            a_rule += 1

    logger.info("supplier strategy defaults: seeded %d, kept %d, retired-respected %d, "
                "derived-skipped %d | analyst: %d suppressions, %d rules, %d skipped",
                seeded, kept, retired, derived, a_sup, a_rule, a_skip)
    return {"seeded": seeded, "kept": kept, "retired": retired,
            "derived_not_constant": derived,
            "analyst_suppressions": a_sup, "analyst_rules": a_rule,
            "analyst_skipped": a_skip,
            "open_items": doc.get("open_items", [])}


# ── The 03-Aug Customer documents ────────────────────────────────────────────

_CUSTOMER_03AUG = _DATA / "customer_mapping_03aug.json"


async def seed_customer_mapping_03aug() -> dict:
    """The two Customer documents of 03-Aug-2026, as dated entries in the one store.

    "NXT Customer Field Mapping 1.xlsx" carries the source→target columns (26 of
    its 243 rows are coloured Mapped); "customer_mapping.txt" carries the
    transformation rules and the constant defaults. They are one instruction given
    on one day, so they are one file here and one date.

    THERE IS NO FAN-OUT, AND THAT IS THE POINT. The analyst asked for these to
    "apply automatically to all existing projects and future projects". Under the
    one dated store that is not something a seeder does — it is what the store IS.
    Every entry is keyed (client, source system, target field) and carries
    effective_date 2026-08-03; every conversion, old or new, resolves against the
    same rows at generate time. Copying them onto today's conversions would create
    exactly the per-project forks the store was built to end, and would reach no
    project created tomorrow.

    Four kinds of statement, one writer:
      * ``derive``   → a column mapping
      * ``constant`` → a default value
      * ``blank``    → a suppression
      * ``rule``     → a transformation rule, config and all

    SCOPE. Every row is scoped to the 19 sheets of the Fusion Customer Import
    interface (``_sheets``), narrowed further where the analyst narrowed it. That
    is not a precedence tier — the sheet list is part of what the document says, it
    is a document about the Customer interface. It is also load-bearing: measured
    against the shipped JSON, 14 of these 53 target fields are also claimed by the
    supplier, HCM or catalog documents, and these being the newest statements in
    the store, an unscoped "Payment Terms = IMMEDIATE" would have become the answer
    for Supplier Site too.

    Idempotent, honours tombstones, and re-runnable: ``record_decision`` compares
    dates, so a redeploy re-stating 03-Aug over an analyst's later edit changes
    nothing.
    """
    import json as _json
    if not _CUSTOMER_03AUG.exists():
        return {"seeded": 0, "note": "customer_mapping_03aug.json not found"}
    try:
        doc = _json.loads(_CUSTOMER_03AUG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}

    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    label = doc.get("_label") or "NXT Customer mapping (03-Aug-2026)"
    doc_sheets = list(doc.get("_sheets") or [])
    erp = "netsuite"

    _ACTIONS = {
        "derive": mapping_store.SOURCE_COLUMN,
        "constant": mapping_store.DEFAULT_VALUE,
        "blank": mapping_store.SUPPRESS,
        "rule": mapping_store.RULE,
    }
    # Keyed by the stored `kind` vocabulary, because the tally below increments
    # `counts[DECISION_TO_KIND[decision]]` and DECISION_TO_KIND yields the kinds
    # ("example_default", "suppress_field"), NOT the decision names. Using the
    # decision names here raised KeyError on the first `constant`/`blank` row,
    # which aborted the whole seed — so no 03-Aug default or suppression ever
    # reached the store, and the derived constants shipped only via the overlay.
    counts = {"column_mapping": 0, "example_default": 0, "suppress_field": 0, "rule": 0}
    retired = skipped = 0
    errors: list[dict] = []

    for r in doc.get("rules") or []:
        decision = _ACTIONS.get((r.get("action") or "").strip())
        tgt = (r.get("target_field") or "").strip()
        if not decision or not tgt:
            skipped += 1
            continue
        # A row narrows the document's scope or inherits it whole; it never widens
        # it. `record_decision` UNIONS sheet lists with whatever the stored row
        # already carries, so an exclusion an earlier document put on this field
        # survives being restated here — which is why the three keys CW_Issues 2
        # scoped on 29-Jul name their exclusions again rather than trusting that.
        sheets = list(r.get("sheets") or doc_sheets)
        value = (r.get("source_column") or "").strip() or None
        if decision == mapping_store.DEFAULT_VALUE:
            value = r.get("value")
        elif decision == mapping_store.RULE and not value:
            # A CONCAT/CASE_WHEN/SEQUENCE has no single source column. "(rule)" is
            # the spelling `rule_authoring_service` already uses for this, and
            # `apply_learned_to_conversion` now lets a rule through on the columns
            # its CONFIG names rather than on this one.
            value = "(rule)"
        # PER-RULE RESILIENCE. One field whose write raises must not abort the whole
        # document — that turned a single production-data quirk on one derive/rule row
        # into "every constant and blank after it is missing", which is exactly the
        # shape this seed shipped: the derive/rule rows landed, then one row threw and
        # the 27 defaults + 2 suppressions after them never got written. Each row is
        # now isolated; a failure is recorded and named in `errors` so it is visible
        # (the reseed endpoint returns it) rather than silently swallowed by the
        # startup try/except, and the remaining rows still land.
        try:
            row = await mapping_store.record_decision(
                decision=decision, target_field=tgt, value=value,
                client_id=nid, source_erp=erp, effective_date=eff,
                captured_from=label, captured_by=None,
                rule_type=r.get("rule_type"),
                rule_config={**(r.get("rule_config") or {}),
                             "note": r.get("note") or ""},
                target_object=r.get("target_object") or "Customer",
                sheets=sheets, exclude_sheets=list(r.get("exclude_sheets") or []),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            errors.append({"target_field": tgt, "error": f"{type(exc).__name__}: {exc}"})
            logger.exception("customer 03-Aug seed: %r failed to record", tgt)
            continue
        if row is None:
            retired += 1        # the analyst retired this — do not resurrect
            continue
        counts[mapping_store.DECISION_TO_KIND[decision]] += 1

    excluded = 0
    for x in doc.get("exclude_source_columns") or []:
        col = (x.get("source_column") or "").strip()
        if not col:
            continue
        row = await mapping_store.record_source_exclusion(
            target_object="Customer", source_column=col, captured_from=label,
            client_id=nid, source_erp=erp,
        )
        if row is not None:
            excluded += 1

    out = {**counts, "retired": retired, "skipped": skipped,
           "excluded_source_columns": excluded,
           "errors": errors, "error_count": len(errors),
           "effective_date": doc.get("_effective_date"),
           "open_questions": len(doc.get("_open_questions") or [])}
    logger.info("customer 03-Aug mapping seed: %s", out)
    return out


_CUSTOMER_06AUG = _DATA / "customer_mapping_06aug.json"


async def seed_customer_mapping_06aug() -> dict:
    """The 06-Aug Customer yellow-column changes from 01_Customer_Import.xlsx.

    Carries the CHANGES and genuinely new columns the workbook flagged (Party Original
    System Reference -> internalid on Parties; Site Language / Primary Indicator kept
    blank; From Date coalesce; Relationship Source System Reference concat), dated
    06-Aug so they win over the 03-Aug statements they revise. Client-scoped and
    sheet-scoped, so every NextPower Customer conversion resolves them at generate
    time — the same pipeline for the FBDI and the CSV output. Per-row resilient +
    returns errors, like the other seeds.
    """
    import json as _json
    if not _CUSTOMER_06AUG.exists():
        return {"seeded": 0, "note": "customer_mapping_06aug.json not found"}
    try:
        doc = _json.loads(_CUSTOMER_06AUG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}

    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    label = doc.get("_label") or "NXT Customer mapping (06-Aug-2026)"
    erp = "netsuite"
    _ACTIONS = {
        "derive": mapping_store.SOURCE_COLUMN,
        "constant": mapping_store.DEFAULT_VALUE,
        "blank": mapping_store.SUPPRESS,
        "rule": mapping_store.RULE,
    }
    counts = {"column_mapping": 0, "example_default": 0, "suppress_field": 0, "rule": 0}
    retired = skipped = 0
    errors: list[dict] = []

    for r in doc.get("rules") or []:
        decision = _ACTIONS.get((r.get("action") or "").strip())
        tgt = (r.get("target_field") or "").strip()
        if not decision or not tgt:
            skipped += 1
            continue
        value = (r.get("source_column") or "").strip() or None
        if decision == mapping_store.DEFAULT_VALUE:
            value = r.get("value")
        elif decision == mapping_store.RULE and not value:
            value = "(rule)"
        try:
            row = await mapping_store.record_decision(
                decision=decision, target_field=tgt, value=value,
                client_id=nid, source_erp=erp, effective_date=eff,
                captured_from=label, captured_by=None,
                rule_type=r.get("rule_type"),
                rule_config={**(r.get("rule_config") or {}), "note": r.get("note") or ""},
                target_object=r.get("target_object") or "Customer",
                sheets=list(r.get("sheets") or []),
                exclude_sheets=list(r.get("exclude_sheets") or []),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            errors.append({"target_field": tgt, "error": f"{type(exc).__name__}: {exc}"})
            logger.exception("customer 06-Aug seed: %r failed to record", tgt)
            continue
        if row is None:
            retired += 1
            continue
        counts[mapping_store.DECISION_TO_KIND[decision]] += 1

    out = {**counts, "retired": retired, "skipped": skipped,
           "errors": errors, "error_count": len(errors),
           "effective_date": doc.get("_effective_date")}
    logger.info("customer 06-Aug mapping seed: %s", out)
    return out


_CUSTOMER_07AUG = _DATA / "customer_mapping_07aug.json"


async def seed_customer_mapping_07aug() -> dict:
    """The 07-Aug Customer additions confirmed by a live test on Customer 03082026.

    Two newly-mapped columns the 03-/06-Aug documents never carried and that the live
    output still shipped blank: Account Description <- companyname (HZ_IMP_ACCOUNTS_T)
    and Identifying Address (GROUP_FIRST_FLAG on entityid, HZ_IMP_PARTYSITES_T). Dated
    07-Aug so they win over the earlier documents. Same one-store, per-row-resilient
    path as the other customer seeds.
    """
    import json as _json
    if not _CUSTOMER_07AUG.exists():
        return {"seeded": 0, "note": "customer_mapping_07aug.json not found"}
    try:
        doc = _json.loads(_CUSTOMER_07AUG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}

    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    label = doc.get("_label") or "NXT Customer mapping (07-Aug-2026)"
    erp = "netsuite"
    _ACTIONS = {
        "derive": mapping_store.SOURCE_COLUMN,
        "constant": mapping_store.DEFAULT_VALUE,
        "blank": mapping_store.SUPPRESS,
        "rule": mapping_store.RULE,
    }
    counts = {"column_mapping": 0, "example_default": 0, "suppress_field": 0, "rule": 0}
    retired = skipped = 0
    errors: list[dict] = []

    for r in doc.get("rules") or []:
        decision = _ACTIONS.get((r.get("action") or "").strip())
        tgt = (r.get("target_field") or "").strip()
        if not decision or not tgt:
            skipped += 1
            continue
        value = (r.get("source_column") or "").strip() or None
        if decision == mapping_store.DEFAULT_VALUE:
            value = r.get("value")
        elif decision == mapping_store.RULE and not value:
            value = "(rule)"
        try:
            row = await mapping_store.record_decision(
                decision=decision, target_field=tgt, value=value,
                client_id=nid, source_erp=erp, effective_date=eff,
                captured_from=label, captured_by=None,
                rule_type=r.get("rule_type"),
                rule_config={**(r.get("rule_config") or {}), "note": r.get("note") or ""},
                target_object=r.get("target_object") or "Customer",
                sheets=list(r.get("sheets") or []),
                exclude_sheets=list(r.get("exclude_sheets") or []),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            errors.append({"target_field": tgt, "error": f"{type(exc).__name__}: {exc}"})
            logger.exception("customer 07-Aug seed: %r failed to record", tgt)
            continue
        if row is None:
            retired += 1
            continue
        counts[mapping_store.DECISION_TO_KIND[decision]] += 1

    out = {**counts, "retired": retired, "skipped": skipped,
           "errors": errors, "error_count": len(errors),
           "effective_date": doc.get("_effective_date")}
    logger.info("customer 07-Aug mapping seed: %s", out)
    return out


_SUPPLIER_TRANSFORMS_06AUG = _DATA / "supplier_transforms_06aug.json"


async def seed_supplier_transforms_06aug() -> dict:
    """The 06-Aug Supplier transform rules (#1 Parent Supplier Name, #3 Supplier Site).

    Recorded as dated RULE decisions in the one store, client-scoped and scoped to the
    Supplier interface object each rule names, so they apply to every NextPower
    supplier conversion of that object — current and future — the same way the Customer
    document does. Per-row resilient and observable (returns `errors`), like the
    customer seed, so one bad row cannot take the rest down silently.
    """
    import json as _json
    if not _SUPPLIER_TRANSFORMS_06AUG.exists():
        return {"seeded": 0, "note": "supplier_transforms_06aug.json not found"}
    try:
        doc = _json.loads(_SUPPLIER_TRANSFORMS_06AUG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"seeded": 0, "error": str(exc)}

    nid = await _nextpower_client_id()
    eff = _effective_date_of(doc)
    label = doc.get("_label") or "NXT Supplier transforms (06-Aug-2026)"
    # SOURCE-AGNOSTIC (was "netsuite"). Tagging these to one source ERP is exactly the
    # "the fix only applied to one project and the issue came back on another" failure:
    # the eBOS supplier project resolves a different source and never received the
    # netsuite-tagged Parent Supplier / Supplier Site rules. These are CLIENT standards
    # for NextPower supplier data regardless of which system it was extracted from, so
    # they are scoped by CLIENT only and reach every current and future supplier
    # conversion. They are safe cross-source: a rule whose columns a given extract does
    # not carry (e.g. Parent Vendor Id on eBOS) simply resolves to its default.
    erp = None

    _ACTIONS = {
        "derive": mapping_store.SOURCE_COLUMN,
        "constant": mapping_store.DEFAULT_VALUE,
        "blank": mapping_store.SUPPRESS,
        "rule": mapping_store.RULE,
    }
    counts = {"column_mapping": 0, "example_default": 0, "suppress_field": 0, "rule": 0}
    retired = skipped = 0
    errors: list[dict] = []

    for r in doc.get("rules") or []:
        decision = _ACTIONS.get((r.get("action") or "").strip())
        tgt = (r.get("target_field") or "").strip()
        if not decision or not tgt:
            skipped += 1
            continue
        value = (r.get("source_column") or "").strip() or None
        if decision == mapping_store.DEFAULT_VALUE:
            value = r.get("value")
        elif decision == mapping_store.RULE and not value:
            value = "(rule)"
        try:
            row = await mapping_store.record_decision(
                decision=decision, target_field=tgt, value=value,
                client_id=nid, source_erp=erp, effective_date=eff,
                captured_from=label, captured_by=None,
                rule_type=r.get("rule_type"),
                rule_config={**(r.get("rule_config") or {}), "note": r.get("note") or ""},
                target_object=r.get("target_object") or "Supplier Import",
                sheets=list(r.get("sheets") or []),
                exclude_sheets=list(r.get("exclude_sheets") or []),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not lose the rest
            errors.append({"target_field": tgt, "error": f"{type(exc).__name__}: {exc}"})
            logger.exception("supplier 06-Aug seed: %r failed to record", tgt)
            continue
        if row is None:
            retired += 1
            continue
        counts[mapping_store.DECISION_TO_KIND[decision]] += 1

    out = {**counts, "retired": retired, "skipped": skipped,
           "errors": errors, "error_count": len(errors),
           "effective_date": doc.get("_effective_date")}
    logger.info("supplier 06-Aug transforms seed: %s", out)
    return out


def customer_03aug_open_questions() -> list[str]:
    """What the analyst still has to confirm about the 03-Aug documents.

    Surfaced rather than buried in a comment: two of these change what the
    generated file contains, and the file looks equally plausible either way.
    """
    import json as _json
    try:
        return list(_json.loads(_CUSTOMER_03AUG.read_text(encoding="utf-8"))
                    .get("_open_questions") or [])
    except Exception:  # noqa: BLE001
        return []
