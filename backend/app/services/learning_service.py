"""Capture and re-apply human-approved mapping decisions (async/Beanie)."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

from beanie import PydanticObjectId

from app.models.conversion import Conversion
from app.models.dataset import DatasetColumnProfile
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.models.transformation import TransformationRule

logger = logging.getLogger(__name__)

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")

REFERENCE_KEY_FIELDS: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Inventory Item Name", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer Number"],
    "Supplier": ["SupplierNumber", "Supplier Number"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code"],
}


def _normalize(name: str | None) -> str:
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", name.lower())


def _is_master_key_field(target_object: str | None, target_field: str | None) -> bool:
    if not target_object or not target_field:
        return False
    return target_field in REFERENCE_KEY_FIELDS.get(target_object, [])


async def _business_object_for(conversion: Conversion) -> str | None:
    if conversion.template_id:
        tpl = await FBDITemplate.get(conversion.template_id)
        if tpl and tpl.business_object:
            return tpl.business_object
    return conversion.target_object


async def source_erp_for_conversion(conversion) -> str | None:
    """The legacy system a conversion reads FROM, used to scope learnings.

    Dataset first, then the project: a project can be pinned to one source while
    an individual conversion is fed by a file extracted from another, and the
    dataset is the more specific fact.
    """
    try:
        from app.models.dataset import Dataset
        for did in (getattr(conversion, "source_dataset_ids", None) or []):
            ds = await Dataset.get(did)
            if ds and getattr(ds, "source_system", None):
                return ds.source_system
    except Exception:                                           # noqa: BLE001
        pass
    try:
        from app.models.project import Project
        pid = getattr(conversion, "project_id", None)
        if pid:
            proj = await Project.get(pid)
            if proj and getattr(proj, "source_system", None):
                return proj.source_system
    except Exception:                                           # noqa: BLE001
        pass
    return None


def sheet_allowed(learning, sheet_name: str | None) -> bool:
    """May this learning touch this interface sheet?

    Oracle repeats a field name across sheets — Customer has 19 — and learnings
    are keyed by name, so one approval reached all of them. That is right for
    ``id -> Party Original System Reference`` and wrong for the same field on
    HZ_IMP_CLASSIFICS_T, and wrong for Receipt Method on the banks sheet where it
    must stay blank.

    Empty lists mean every sheet: that is the behaviour every existing row was
    captured under, so turning this on changes nothing until someone narrows a
    learning deliberately. Exclusion wins over inclusion — a sheet named in both
    is excluded, because a person listing it under "never" is stating the
    stronger intent.
    """
    only = [s for s in (getattr(learning, "sheets", None) or []) if str(s).strip()]
    never = [s for s in (getattr(learning, "exclude_sheets", None) or []) if str(s).strip()]
    if not only and not never:
        return True
    name = _normalize(sheet_name)
    if not name:
        # Unknown sheet: allow when the learning only EXCLUDES (nothing says this
        # is the excluded one), refuse when it names an allow-list it cannot be
        # shown to be part of.
        return not only
    if any(_normalize(s) == name for s in never):
        return False
    return not only or any(_normalize(s) == name for s in only)


def source_scope(source_erp: str | None) -> dict:
    """Read filter: this source's learnings PLUS the source-agnostic ones.

    Item maps differently out of NetSuite than out of SyteLine, so a learning
    captured from one must not be handed to a conversion reading the other.
    Keying on (object, field, client) alone let the two collide on write and
    cross over on read.

    Legacy rows carry no ``source_erp`` — that is every learning captured before
    this scoping existed. They are still returned, because filtering strictly
    would silently strand the entire existing library. New captures are always
    stamped, so the untagged set only shrinks.
    """
    if not source_erp:
        return {}
    return {"$or": [{"source_erp": source_erp},
                    {"source_erp": None}, {"source_erp": {"$exists": False}}]}


async def _upsert_learned(kind, business_object, field_name, *, original, resolved,
                          rule_type=None, rule_config=None, captured_from="auto-capture",
                          client_id=None, source_erp=None):
    """Upsert one reusable object-level learned rule. Never downgrades a rule
    captured from a gold example / prompt / accepted crosswalk with an
    auto-captured one (human/gold signals outrank auto-capture).

    Captured learnings are CLIENT-SCOPED (is_global=False, client_id set) — they
    encode one client's source data, so they must not leak to other clients. The
    existing-row lookup is keyed by client too, so two clients keep independent
    rules for the same object/field.

    It is keyed by SOURCE SYSTEM as well: NetSuite's Item mapping is not
    SyteLine's, so without this the second capture overwrites the first and both
    conversions then inherit whichever was written last."""
    if not business_object or not field_name:
        return False
    existing = await LearnedMapping.find_one(
        LearnedMapping.kind == kind,
        LearnedMapping.target_object == business_object,
        LearnedMapping.target_field == field_name,
        LearnedMapping.client_id == client_id,
        LearnedMapping.source_erp == source_erp,
        include_deleted=True,
    )
    # Retired by the user — auto-capture after Generate Output must not bring it
    # back (QA issue #5). include_deleted above is what makes the tombstone
    # visible here; without it we would insert a fresh duplicate.
    if existing and getattr(existing, "is_deleted", False):
        return False
    if existing and existing.captured_from in ("gold example", "prompt", "value-map-accept") \
            and captured_from == "auto-capture":
        return False
    category = _category_for(rule_type) if kind == "column_mapping" else kind
    doc = {
        "kind": kind, "category": category,
        "original_value": str(original), "resolved_value": str(resolved),
        "target_object": business_object, "target_field": field_name,
        "rule_type": rule_type, "rule_config": rule_config or {},
        "client_id": client_id, "is_global": False,
        "source_erp": source_erp,
        "captured_from": captured_from, "captured_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(doc)
    else:
        await LearnedMapping(**doc).insert()
    return True


async def capture_learnings_from_conversion(conversion: Conversion) -> dict:
    """After a successful mapping/output, persist the conversion's effective
    mappings, constant defaults and suppressions as reusable object-level
    learnings so future conversions of the same object reuse them (and lean on
    AI less). Only trustworthy signals are captured — user-approved/overridden,
    deterministic/high-confidence (>=0.85), or suppressed — so low-confidence AI
    guesses don't get propagated."""
    business_object = await _business_object_for(conversion)
    if not business_object or not conversion.template_id:
        return {"captured": 0}
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    # Learnings are keyed by source system too — see source_scope.
    _src = await source_erp_for_conversion(conversion)
    fields = {f.id: f.field_name for f in await FBDIField.find(
        FBDIField.template_id == conversion.template_id).to_list()}
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id).to_list()
    _PRIO = {"overridden": 4, "approved": 3, "not_applicable": 2, "rejected": 1, "suggested": 0}
    best: dict = {}
    for m in maps:
        c = best.get(m.target_field_id)
        if c is None or _PRIO.get(m.status or "suggested", 0) > _PRIO.get(c.status or "suggested", 0):
            best[m.target_field_id] = m
    n = 0
    for m in best.values():
        fname = fields.get(m.target_field_id)
        if not fname:
            continue
        # Capture user-confirmed mappings and reasonably confident ones
        # (rule-based confident matches sit ~0.6+). Gold/prompt rules are never
        # downgraded by _upsert_learned, so this only ADDS coverage over time.
        trustworthy = (m.status in ("approved", "overridden")) or ((m.confidence or 0) >= 0.60)
        _dv = (str(m.default_value).strip() if m.default_value is not None else "")
        if m.status == "not_applicable" and not _dv:
            # Genuinely "leave blank". A not_applicable mapping that ALSO carries an
            # explicit default is intent to POPULATE (the output writer already
            # treats it that way), so it must be learned as a default rather than
            # filed under "left blank on purpose" — QA issue #7, where analyst
            # defaults never appeared in the Learning Centre.
            if await _upsert_learned("suppress_field", business_object, fname,
                                     original="(blank)", resolved="", rule_type="suppress",
                                     client_id=_cid, source_erp=_src):
                n += 1
        elif m.source_column and trustworthy:
            st = m.suggested_transformation or {}
            if await _upsert_learned("column_mapping", business_object, fname,
                                     original=m.source_column, resolved=fname,
                                     rule_type=st.get("rule_type"), rule_config=st.get("config"),
                                     client_id=_cid, source_erp=_src):
                n += 1
        elif _dv and (trustworthy or m.status == "not_applicable"):
            # An explicit default the analyst typed is a decision regardless of the
            # mapping's confidence — capture it (issue #7).
            if await _upsert_learned("example_default", business_object, fname,
                                     original="(default)", resolved=_dv,
                                     rule_type="default", client_id=_cid,
                                     source_erp=_src):
                n += 1
    return {"captured": n, "object": business_object}


def _category_for(rule_type: str | None) -> str:
    if not rule_type:
        return "Column Mapping Alias"
    rt = rule_type.upper()
    if rt == "DATE_FORMAT":
        return "Date Format Rule"
    if rt in ("VALUE_MAP", "CROSSWALK_LOOKUP", "CASE_WHEN", "CONDITIONAL"):
        return "Status Value Mapping"
    if rt in ("CONSTANT", "DEFAULT_VALUE", "COMPUTED", "COALESCE"):
        return "Default & Computed Value"
    if rt in ("ARITHMETIC", "NUMBER_FORMAT"):
        return "Numeric Rule"
    return "Column Mapping Alias"


async def _upsert(*, kind, category, original_value, resolved_value,
                  target_object=None, target_field=None, rule_type=None,
                  rule_config=None, project_id=None, captured_from, captured_by,
                  client_id=None, source_erp=None,
                  revive: bool = False) -> LearnedMapping | None:
    """Insert or update a learning.

    ``revive=False`` (the default) honours the tombstone from QA issue #5: if the
    user deleted this learning, an automatic path — auto-capture after Generate,
    a startup seed, an approve/override — must NOT bring it back. Only an explicit
    user action passes ``revive=True``. Returns ``None`` when a tombstoned row was
    left untouched.
    """
    # Client-scoped (is_global=False): an interactively captured rule encodes one
    # client's source data. Dedup is keyed by client too, so clients stay separate.
    # source_erp is part of the identity: the same target field is fed by a
    # different column depending on which legacy system the extract came from,
    # so NetSuite's Item rule and SyteLine's must be two rows, not one.
    query = {
        "kind": kind,
        "target_object": target_object,
        "target_field": target_field,
        "rule_type": rule_type,
        "client_id": client_id,
        "source_erp": source_erp,
    }
    norm_orig = _normalize(original_value)
    # include_deleted=True is LOAD-BEARING. LearnedMapping.find injects
    # {'is_deleted': {'$ne': True}}, so without it a tombstoned row is invisible
    # here, the is_deleted check below can never fire, the loop finds nothing and
    # this falls through to INSERT a fresh duplicate — resurrecting the learning the
    # analyst deleted, on the next approve / default / rule save that touches the
    # field. Third instance of CW #5, this time on the interactive path.
    existing = await LearnedMapping.find(query, include_deleted=True).to_list()
    for lm in existing:
        if _normalize(lm.original_value) == norm_orig:
            if getattr(lm, "is_deleted", False) and not revive:
                # User retired this learning — respect that and do not resurrect.
                return None
            patch = {
                "resolved_value": resolved_value, "rule_config": rule_config or {},
                "captured_from": captured_from, "captured_by": captured_by,
                "captured_at": datetime.utcnow(),
                "project_id": project_id, "client_id": client_id, "is_global": False,
                "source_erp": source_erp,
            }
            if revive and getattr(lm, "is_deleted", False):
                patch.update({"is_deleted": False, "deleted_at": None, "deleted_by": None})
            await lm.set(patch)
            return lm
    lm = LearnedMapping(
        kind=kind, category=category, original_value=original_value,
        resolved_value=resolved_value, target_object=target_object,
        target_field=target_field, rule_type=rule_type, rule_config=rule_config or {},
        project_id=project_id, captured_from=captured_from, captured_by=captured_by,
        client_id=client_id, is_global=False, source_erp=source_erp,
    )
    await lm.insert()
    return lm


async def record_learning_from_mapping(
    mapping: MappingSuggestion, conversion: Conversion, captured_by: str | None
) -> LearnedMapping | None:
    # A mapping with no source column but an explicit default IS a decision worth
    # learning — "Include in Credit Check = Y" is exactly the kind of standard the
    # library exists to hold. Previously this returned early, so defaults set in
    # Mapping Review only reached the Learning Centre after a Generate Output ran
    # (QA issue #7: "Default values in the learning centre is not getting populated").
    _default = (str(mapping.default_value).strip()
                if getattr(mapping, "default_value", None) is not None else "")
    if not mapping.source_column and not _default:
        return None
    business_object = await _business_object_for(conversion)
    if not business_object:
        return None
    tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    target_field = None
    if tpl:
        fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
        for f in fields:
            if f.id == mapping.target_field_id:
                target_field = f.field_name
                break
    if not target_field:
        return None
    rule_type = None
    rule_config: dict = {}
    if mapping.suggested_transformation and isinstance(mapping.suggested_transformation, dict):
        rule_type = mapping.suggested_transformation.get("rule_type")
        rule_config = mapping.suggested_transformation.get("config", {})
    captured_from = f"{conversion.name} -- {target_field}"
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    # Learnings are keyed by source system too — see source_scope.
    _src = await source_erp_for_conversion(conversion)
    if not mapping.source_column:
        # Default-only decision → an example_default learning, so it shows up in
        # the Learning Centre's "Default values" tab immediately on save.
        return await _upsert(
            kind="example_default", category="Default Value",
            original_value="(default)", resolved_value=_default,
            target_object=business_object, target_field=target_field,
            rule_type="default", rule_config={"default_value": _default},
            project_id=conversion.project_id, client_id=_cid, source_erp=_src,
            captured_from=captured_from, captured_by=captured_by,
        )
    lm = await _upsert(
        kind="column_mapping", category="Column Mapping Alias",
        original_value=mapping.source_column, resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule_type, rule_config=rule_config,
        project_id=conversion.project_id, client_id=_cid, source_erp=_src,
        captured_from=captured_from, captured_by=captured_by,
    )
    if rule_type and _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule_type, rule_config=rule_config,
            project_id=conversion.project_id, client_id=_cid, source_erp=_src,
            captured_from=captured_from, captured_by=captured_by,
        )
    return lm


async def record_learning_from_rule(
    rule: TransformationRule, conversion: Conversion, captured_by: str | None
) -> LearnedMapping | None:
    business_object = await _business_object_for(conversion)
    if not business_object:
        return None
    target_field = None
    if rule.target_field_id:
        f = await FBDIField.get(rule.target_field_id)
        if f:
            target_field = f.field_name
    if not target_field:
        return None
    captured_from = f"{conversion.name} -- {target_field} (manual)"
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    # Learnings are keyed by source system too — see source_scope.
    _src = await source_erp_for_conversion(conversion)
    lm = await _upsert(
        kind="rule", category=_category_for(rule.rule_type),
        original_value=rule.source_column or "", resolved_value=target_field,
        target_object=business_object, target_field=target_field,
        rule_type=rule.rule_type, rule_config=rule.rule_config or {},
        project_id=conversion.project_id, client_id=_cid, source_erp=_src,
        captured_from=captured_from, captured_by=captured_by,
    )
    if _is_master_key_field(business_object, target_field):
        await _upsert(
            kind="reference_standard", category="Reference Key Standard",
            original_value=target_field, resolved_value=target_field,
            target_object=business_object, target_field=target_field,
            rule_type=rule.rule_type, rule_config=rule.rule_config or {},
            project_id=conversion.project_id, client_id=_cid, source_erp=_src,
            captured_from=captured_from, captured_by=captured_by,
        )
    return lm


ANALYST_APPROVED = "analyst-approved"


async def propagate_learning_to_open_conversions(
    lm: "LearnedMapping", origin: Conversion, *, captured_by: str | None = None,
) -> dict:
    """Push one analyst correction onto the conversions that already exist.

    Analyst, 30-Jul: "any rule or correction I make apply in learning so that it will be
    available for all current and future conversions". Capturing the learning already
    covered FUTURE conversions — they read the library when they are created. The ones
    that already exist did not, so a correction made today reached everything except the
    work in front of you, which is the case that matters most.

    What it will and will not touch, deliberately:
      * still-suggested mappings, and mappings the ENGINE auto-approved -> updated.
      * anything a PERSON approved or overrode -> left exactly as it is. Two analysts
        can disagree; a library entry must not silently overwrite a colleague's
        decision. They see it as a suggestion on the next review instead.
      * the origin conversion itself -> skipped, it is already correct.

    Affected outputs are marked stale rather than regenerated: the file on disk no
    longer matches the rules that would now run, and saying so is honest where
    regenerating dozens of files behind the analyst's back is not.
    """
    from app.models.fbdi import FBDIField
    from app.services.client_service import client_id_for_conversion

    if lm is None or not lm.target_field:
        return {"conversions": 0, "mappings": 0, "stale_outputs": 0}
    business_object = await _business_object_for(origin)
    if not business_object:
        return {"conversions": 0, "mappings": 0, "stale_outputs": 0}
    cid = await client_id_for_conversion(origin)
    src = await source_erp_for_conversion(origin)

    touched_convs = touched_maps = staled = 0
    now = datetime.utcnow()
    for conv in await Conversion.find_all().to_list():
        if conv.id == origin.id or not conv.template_id:
            continue
        if await _business_object_for(conv) != business_object:
            continue
        if await client_id_for_conversion(conv) != cid:
            continue
        # Source-system scope, same rule the apply pass uses: a SyteLine conversion
        # must not inherit a NetSuite correction.
        csrc = await source_erp_for_conversion(conv)
        if src and csrc and csrc != src:
            continue
        fields = await FBDIField.find(FBDIField.template_id == conv.template_id).to_list()
        ids = {f.id for f in fields if _norm_field(f.field_name) == _norm_field(lm.target_field)}
        if not ids:
            continue
        hit = False
        for m in await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id).to_list():
            if m.target_field_id not in ids:
                continue
            approver = (getattr(m, "approved_by", None) or "")
            if m.status in ("overridden",) or (
                    m.status == "approved" and approver != "learning-engine"):
                continue        # a person decided this — leave it alone
            patch = {"status": "approved", "review_required": 0,
                     "approved_by": "learning-engine", "approved_at": now}
            if lm.kind == "column_mapping" and lm.original_value:
                patch["source_column"] = lm.original_value
            elif lm.kind == "example_default":
                patch["default_value"] = lm.resolved_value
            if lm.rule_type:
                patch["suggested_transformation"] = {
                    "rule_type": lm.rule_type, "config": lm.rule_config or {}}
            await m.set(patch)
            touched_maps += 1
            hit = True
        if hit:
            touched_convs += 1
            from app.models.output import ConvertedOutput
            res = await ConvertedOutput.find(
                ConvertedOutput.conversion_id == conv.id).update(
                    {"$set": {"status": "stale"}})
            staled += int(getattr(res, "modified_count", 0) or 0)
    return {"conversions": touched_convs, "mappings": touched_maps,
            "stale_outputs": staled, "target_field": lm.target_field,
            "captured_by": captured_by}


def _effective_of(lm) -> datetime:
    """When this learning's INSTRUCTION was given.

    Analyst, 30-Jul: "for conflicts always the latest one should be taken". Falls
    back to captured_at, which for a UI capture is the moment the analyst acted;
    seeded rows carry the effective date of the file they came from, so a redeploy
    cannot make the 13-Jul strategy look newer than the 30-Jul corrections.
    """
    return (getattr(lm, "effective_date", None)
            or getattr(lm, "captured_at", None)
            or datetime.min)


def _candidate_order(lm, strong: set) -> tuple:
    """Sort key for competing learnings: LATEST FIRST, then strength.

    Date leads because the analyst said so, and because a gold example captured
    weeks ago was beating the mapping workbook they had just handed over — which
    is exactly the "why is the tool still following gold learnings" report. A
    transform still beats a plain alias, but only among instructions given on the
    same day: strength is a tie-break, not the primary rule.
    """
    return (-_effective_of(lm).timestamp(),
            0 if (getattr(lm, "rule_type", "") or "").upper() in strong else 1)


def _norm_field(s) -> str:
    """Oracle decorates headers with '*' and stray spaces; the library stores the
    plain name. Matching on the raw string silently misses every required field."""
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", str(s or "").lower())


async def enforce_blank_correction(
    target_object: str, target_field: str, *,
    client_id=None, captured_from: str = "analyst correction",
    captured_by: str = "analyst-correction",
) -> dict:
    """One field. Thin wrapper over the batch form — see it for the reasoning."""
    res = await enforce_blank_corrections(
        [(target_object, target_field)], client_id=client_id,
        captured_from=captured_from, captured_by=captured_by)
    return res["fields"][0] if res["fields"] else {
        "field": target_field, "object": target_object, "learnings_retired": 0,
        "mappings_blanked": 0, "skipped_human": 0, "conversions": 0,
        "stale_outputs": 0}


async def enforce_blank_corrections(
    pairs, *, client_id=None, captured_from: str = "analyst correction",
    captured_by: str = "analyst-correction", as_of: "datetime | None" = None,
) -> dict:
    """Make "these columns ship blank" true in the LIBRARY and in the MAPPINGS.

    Analyst, 30-Jul: "Procurement BU blank and Liability Distribution blank, modify
    the learning, the code should change the learning and mapping as I am saying
    now", then "Supplier Name New blank change this in learnings and mappings".

    Recording a suppress_field learning was never enough, and the file they sent
    back proves it. Alongside the new suppression the library still held the OLD
    bindings — Tax Reporting Name, declared blank, carried a column_mapping from a
    gold example, a `rule` from a prior conversion AND an example_mapping, every one
    of them pointing at Legal Name — and every existing conversion still had a
    mapping row using them. Two rows in one library saying opposite things about one
    field is not a rule, it is a coin toss, and the older row kept winning.

    So a blank correction does three things:

      1. RETIRES every contradicting learning — all kinds except the suppression
         itself. Listing kinds is what let this through the first time: the sweep
         covered column_mapping and example_default while `rule` and
         example_mapping sailed past it.
      2. REWRITES the mapping rows in every existing conversion of the object:
         source cleared, default cleared, status not_applicable. This is what makes
         the screen agree with the file.
      3. Marks the affected outputs stale, because the file on disk still has the
         value in it.

    It will NOT touch a mapping a PERSON approved — that is the precedence the
    analyst set, and a seeding pass silently reversing a colleague's decision is
    the failure this rule exists to prevent. Those are counted as ``skipped_human``
    rather than swallowed, so "nothing happened" can never be read as "nothing to
    do".

    BATCHED over all the fields at once, and that is not a micro-optimisation. The
    per-field version re-walked all 232 conversions for each of six blanks across
    six sheet objects — 36 sweeps, two database round-trips per conversion each
    time — and the on-demand reseed simply never returned. One pass over the
    conversions, resolving each one's object and client once and caching template
    fields, does the same work in a fraction of the calls.
    """
    from app.models.fbdi import FBDIField
    from app.models.output import ConvertedOutput
    from app.services.client_service import client_id_for_conversion

    wanted: dict[str, dict[str, str]] = {}          # object -> {norm field: label}
    results: dict[tuple, dict] = {}
    for obj, fld in pairs:
        obj, fld = (obj or "").strip(), (fld or "").strip()
        if not (obj and fld):
            continue
        wanted.setdefault(obj, {})[_norm_field(fld)] = fld
        results[(obj, _norm_field(fld))] = {
            "field": fld, "object": obj, "learnings_retired": 0,
            "mappings_blanked": 0, "skipped_human": 0, "conversions": 0,
            "stale_outputs": 0}
    if not wanted:
        return {"fields": [], "conversions_scanned": 0}

    now = datetime.utcnow()

    # 1 — one pass over the conversions, rewriting the mapping rows.
    #
    # Deliberately BEFORE the library sweep, because the set of clients whose
    # conversions this correction actually reaches is what makes the sweep safe to
    # scope. Retiring by the correction's own client id alone retired NOTHING on
    # the live instance: the analyst docs seed under the bootstrap client while the
    # gold-example rows carry the client of the project they were captured in, so
    # every contradicting row fell outside the filter and survived.
    _fields_by_tpl: dict = {}
    seen_clients: set = {client_id} if client_id is not None else set()
    scanned = 0
    for conv in await Conversion.find_all().to_list():
        if not conv.template_id:
            continue
        obj = await _business_object_for(conv)
        fields = wanted.get(obj or "")
        if not fields:
            continue
        # Same inclusive rule as the library sweep: skip only when BOTH sides name a
        # client and they differ. An unscoped conversion is not another tenant's, and
        # requiring an exact match is why nothing was rewritten on the live instance.
        _cc = await client_id_for_conversion(conv)
        if client_id is not None and _cc is not None and _cc != client_id:
            continue
        if _cc is not None:
            seen_clients.add(_cc)
        scanned += 1
        if conv.template_id not in _fields_by_tpl:
            _fields_by_tpl[conv.template_id] = await FBDIField.find(
                FBDIField.template_id == conv.template_id).to_list()
        ids: dict = {}
        for f in _fields_by_tpl[conv.template_id]:
            nf = _norm_field(f.field_name)
            if nf in fields:
                ids[f.id] = nf
        if not ids:
            continue
        hit = False
        for m in await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id).to_list():
            nf = ids.get(m.target_field_id)
            if nf is None:
                continue
            approver = (getattr(m, "approved_by", None) or "")
            _human = m.status == "overridden" or (
                m.status == "approved" and approver != "learning-engine")
            # ...and only when the person spoke LAST. Analyst, 30-Jul: "for
            # conflicts always the latest one should be taken for mapping". A
            # correction dated after someone's approval is that same analyst
            # changing their mind, and treating the older approval as untouchable
            # is why the live reseed reported nothing but skipped_human.
            if _human and as_of is not None:
                _at = getattr(m, "approved_at", None)
                _human = bool(_at) and _at >= as_of
            if _human:
                results[(obj, nf)]["skipped_human"] += 1
                continue
            if (m.status == "not_applicable" and not m.source_column
                    and not m.default_value):
                continue                   # already blank, nothing to write
            await m.set({
                "source_column": None, "default_value": None,
                "suggested_transformation": None,
                "status": "not_applicable", "review_required": 0,
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f"Kept blank — {captured_from}",
                "updated_at": now,
            })
            results[(obj, nf)]["mappings_blanked"] += 1
            results[(obj, nf)]["conversions"] += 1
            hit = True
        if hit:
            res = await ConvertedOutput.find(
                ConvertedOutput.conversion_id == conv.id).update(
                    {"$set": {"status": "stale"}})
            _n = int(getattr(res, "modified_count", 0) or 0)
            for nf in set(ids.values()):
                results[(obj, nf)]["stale_outputs"] += _n
    # 2 — retire the contradicting library rows, one query per object.
    #
    # Client scope is INCLUSIVE of global rows on purpose. Scoping the sweep to the
    # correction's own client left the global gold-example row alive, and a global
    # row is precisely what reaches this client — so the correction was recorded and
    # then overruled by the thing it was meant to retire. `retired_because` is
    # written onto each row so a global retirement is auditable rather than silent.
    #
    # include_deleted is load-bearing: the default query hides tombstoned rows, so
    # the is_deleted check below would never once fire and every reseed would
    # re-stamp rows that are already retired. That exact dead guard has been found
    # in this file before — the audit in test_tombstone_guards exists because of it.
    for obj, fields in wanted.items():
        for lm in await LearnedMapping.find(
            LearnedMapping.kind != "suppress_field",
            LearnedMapping.target_object == obj,
            include_deleted=True,
        ).to_list():
            if getattr(lm, "is_deleted", False):
                continue                   # already retired; leave the tombstone alone
            nf = _norm_field(lm.target_field)
            if nf not in fields:
                continue
            _lc = getattr(lm, "client_id", None)
            # Global rows (no client) always go: a global row is precisely what
            # reaches this client. A scoped row goes when its client is one whose
            # conversions this correction just rewrote — that is the proof it is in
            # scope, and it is the only thing that distinguishes "our data under a
            # different client id" from "another tenant's row", which is never ours
            # to retire.
            if _lc is not None and seen_clients and _lc not in seen_clients:
                continue
            await lm.set({"is_deleted": True, "deleted_at": now,
                          "deleted_by": captured_by,
                          "rule_config": {**(lm.rule_config or {}),
                                          "retired_because": f"{fields[nf]} is blank "
                                                             f"per {captured_from}"}})
            results[(obj, nf)]["learnings_retired"] += 1

    return {"fields": [v for v in results.values()], "conversions_scanned": scanned}


async def apply_rule_corrections(
    rules, *, client_id=None, captured_from: str = "analyst correction",
    as_of: "datetime | None" = None,
) -> dict:
    """Push a DERIVATION rule onto the conversions that already exist.

    Analyst, 30-Jul: "the rule for supplier site column is Country Code (2 character
    ISO code)-(City value) can you implement this in learnings so that it comes in
    output of existing and future".

    A rule correction was only ever half-applied. It was seeded as a learning, so a
    NEW conversion picked it up, and it was enforced by the write-time overlay, so
    the generated file was right — but the mapping rows of conversions that already
    existed still carried whatever the matcher had guessed. The analyst opens one of
    those, sees the old derivation on screen, and has no reason to believe the file
    says anything different. Blanks already got this treatment; rules did not.

    ``rules`` is an iterable of ``(object, field, rule_type, rule_config)``.

    Same precedence as everywhere else: a mapping a PERSON approved or overrode
    after the correction's date is left alone and counted, not overwritten.
    """
    from app.models.fbdi import FBDIField
    from app.models.output import ConvertedOutput
    from app.services.client_service import client_id_for_conversion

    wanted: dict[str, dict[str, tuple]] = {}
    results: dict[tuple, dict] = {}
    for obj, fld, rtype, rcfg in rules:
        obj, fld = (obj or "").strip(), (fld or "").strip()
        if not (obj and fld and rtype):
            continue
        nf = _norm_field(fld)
        wanted.setdefault(obj, {})[nf] = (fld, rtype, rcfg or {})
        results[(obj, nf)] = {"field": fld, "object": obj, "mappings_updated": 0,
                              "skipped_human": 0, "stale_outputs": 0}
    if not wanted:
        return {"fields": [], "conversions_scanned": 0}

    now = datetime.utcnow()
    _fields_by_tpl: dict = {}
    scanned = 0
    for conv in await Conversion.find_all().to_list():
        if not conv.template_id:
            continue
        obj = await _business_object_for(conv)
        fields = wanted.get(obj or "")
        if not fields:
            continue
        _cc = await client_id_for_conversion(conv)
        if client_id is not None and _cc is not None and _cc != client_id:
            continue
        scanned += 1
        if conv.template_id not in _fields_by_tpl:
            _fields_by_tpl[conv.template_id] = await FBDIField.find(
                FBDIField.template_id == conv.template_id).to_list()
        ids = {f.id: _norm_field(f.field_name)
               for f in _fields_by_tpl[conv.template_id]
               if _norm_field(f.field_name) in fields}
        if not ids:
            continue
        hit = False
        for m in await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id).to_list():
            nf = ids.get(m.target_field_id)
            if nf is None:
                continue
            approver = (getattr(m, "approved_by", None) or "")
            _human = m.status == "overridden" or (
                m.status == "approved" and approver != "learning-engine")
            if _human and as_of is not None:
                _at = getattr(m, "approved_at", None)
                _human = bool(_at) and _at >= as_of
            if _human:
                results[(obj, nf)]["skipped_human"] += 1
                continue
            label, rtype, rcfg = fields[nf]
            _cur = getattr(m, "suggested_transformation", None) or {}
            if (_cur.get("rule_type") == rtype
                    and (_cur.get("config") or {}) == rcfg):
                continue                   # already carries this rule
            await m.set({
                "suggested_transformation": {"rule_type": rtype, "config": rcfg},
                "status": "approved", "review_required": 0,
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f"Derived — {captured_from}",
                "updated_at": now,
            })
            results[(obj, nf)]["mappings_updated"] += 1
            hit = True
        if hit:
            res = await ConvertedOutput.find(
                ConvertedOutput.conversion_id == conv.id).update(
                    {"$set": {"status": "stale"}})
            _n = int(getattr(res, "modified_count", 0) or 0)
            for nf in set(ids.values()):
                results[(obj, nf)]["stale_outputs"] += _n
    return {"fields": list(results.values()), "conversions_scanned": scanned}


async def apply_learned_to_conversion(
    conversion: Conversion, mappings: Iterable[MappingSuggestion], force: bool = False,
) -> int:
    """Apply the object's stored reference standard (column mappings, constant
    defaults, suppressions) to a conversion's mappings.

    Normally only touches still-"suggested" mappings. With ``force=True`` (used at
    generate time and by the explicit "apply gold" action) stored rules also override
    mappings that were approved AUTOMATICALLY — so a stored standard is guaranteed to
    reach the output.

    A HUMAN DECISION IS NEVER OVERRIDDEN (analyst, 30-Jul: "if user modifies anything
    from the tool UI and then approves it, it should get highest precedence"). Before
    this, ``force`` overrode every "approved" mapping regardless of who approved it, so
    an analyst could correct a field in Mapping Review, approve it, generate, and find
    the library had quietly put its own value back — with nothing on screen to say so.
    That is the single most expensive kind of bug in this tool, because the screen and
    the file disagree and the screen looks right.

    The distinction is the approver. The engine stamps its own auto-approvals with
    ``approved_by == "learning-engine"`` (the same marker the delete/revert path keys
    on), so anything else in that field is a person, and a person wins.
    """
    def _eligible(m: MappingSuggestion) -> bool:
        if m.status == "suggested":
            return True
        if not force or m.status != "approved":
            return False
        # Approved by a person → highest precedence, never overwritten.
        return (getattr(m, "approved_by", None) or "") == "learning-engine"

    business_object = await _business_object_for(conversion)
    if not business_object:
        return 0
    # Tenant scope: only this client's learnings + anything global. Prevents a
    # future client inheriting NextPower's source-system mappings.
    from app.services.client_service import client_id_for_conversion, scope_query
    _scope = await scope_query(await client_id_for_conversion(conversion))
    # Scope to the legacy system this conversion actually reads from. Without it
    # a SyteLine Item conversion inherits NetSuite's Item mappings, which point
    # at columns its extract does not have. `$and` rather than merging keys,
    # because scope_query already owns `$or` for the client scope and a second
    # `$or` would overwrite it.
    _src = await source_erp_for_conversion(conversion)
    _srcq = source_scope(_src)

    def _q(kind: str) -> dict:
        base = {"kind": kind, "target_object": business_object}
        if _scope and _srcq:
            return {**base, "$and": [_scope, _srcq]}
        return {**base, **_scope, **_srcq}

    learned = await LearnedMapping.find(_q("column_mapping")).to_list()
    suppressed = await LearnedMapping.find(_q("suppress_field")).to_list()
    if not learned and not suppressed:
        return 0
    # An exact source match beats a legacy untagged row for the same target, so
    # migrating the library does not require touching old rows: the moment a
    # source-specific learning exists it takes over.
    if _src:
        _exact = {lm.target_field for lm in learned if lm.source_erp == _src}
        learned = [lm for lm in learned
                   if lm.source_erp == _src or lm.target_field not in _exact]
    suppressed_targets = {lm.target_field for lm in suppressed if lm.target_field}
    by_target: dict[str, list[LearnedMapping]] = {}
    for lm in learned:
        if lm.target_field:
            by_target.setdefault(lm.target_field, []).append(lm)
    # When a field has several candidate mappings, try the ones that carry a real
    # VALUE TRANSFORM (PHONE_PART, CASE_WHEN, VALUE_MAP, SPLIT…) first. A transform
    # is a deliberate rule; a plain alias or a gold "direct_map" is just a guessed
    # column. Without this, a direct_map (e.g. Phone <- "Address Phone", an empty
    # column) can beat the intended PHONE_PART split of the populated "Phone" column
    # and blank the output. Ties keep original order.
    _STRONG_TRANSFORMS = {
        "PHONE_PART", "CASE_WHEN", "VALUE_MAP", "DATE_FORMAT", "SPLIT", "CONCAT",
        "COALESCE", "CONDITIONAL", "REGEX_EXTRACT", "REGEX_REPLACE", "SUBSTRING",
        "PREFIX", "SUFFIX", "ARITHMETIC", "NUMBER_FORMAT", "CROSSWALK_LOOKUP", "PAD",
        # REPLACE / REMOVE_SPECIAL_CHARS were missing, and that silently cost data:
        # the analyst rule "Taxpayer ID <- tax_id, strip spaces" sorted BELOW a plain
        # alias for the same field, so the alias won and the FBDI shipped
        # "7 5 -2 1 1 0 3 5 7" instead of "75-2110357". A REPLACE is just as
        # deliberate a rule as a VALUE_MAP — rank it accordingly.
        "REPLACE", "REMOVE_SPECIAL_CHARS", "MAP_BOOLEAN", "CONDITIONAL_DATE",
    }
    for _lst in by_target.values():
        _lst.sort(key=lambda lm: _candidate_order(lm, _STRONG_TRANSFORMS))
    # Oracle decorates required/conditional headers with asterisks and stray spaces
    # ("Supplier Name*", "Address Name *", "*Supplier Number", "**Bank Name"), and the
    # analyst mapping docs write the plain name ("Supplier Name"). Matching the learned
    # target_field to the template field_name by EXACT string therefore silently missed
    # every decorated field — the mapping existed in the library and simply never
    # applied. Keep exact match first (unambiguous), then fall back to the normalized
    # name. Ambiguous normalized keys (two different template fields collapsing to the
    # same key) are dropped from the fallback so we never guess.
    # MERGE candidates under the normalized key — do not drop on collision.
    #
    # An earlier version dropped any normalized key that two different learned
    # target_fields mapped onto, meaning to be cautious. That was wrong, and it
    # blanked a REQUIRED field: the library holds both "Supplier Site*" <- city
    # (PREFIX "BU ") from the analyst doc and "Supplier Site" <- "Address Label"
    # from an older doc. Both normalize to "suppliersite", so the key was
    # discarded; exact match then found only the "Address Label" row, whose source
    # column does not exist in an eBOS extract, and Supplier Site shipped empty.
    #
    # A collision here is almost always two SPELLINGS OF THE SAME FIELD, not two
    # different fields — Oracle's own headers repeat and get decorated differently
    # across docs. Merging is safe because the loop below already discards any
    # candidate whose source column is absent from the dataset, and the transform
    # sort above puts deliberate rules ahead of plain aliases.
    by_target_norm: dict[str, list[LearnedMapping]] = {}
    for _tf, _lst in by_target.items():
        k = _normalize(_tf)
        if k:
            by_target_norm.setdefault(k, []).extend(_lst)
    for _lst in by_target_norm.values():
        _lst.sort(key=lambda lm: _candidate_order(lm, _STRONG_TRANSFORMS))
    _suppressed_norm = {_normalize(t) for t in suppressed_targets if _normalize(t)}
    # Precedence: an explicit source→target mapping (analyst doc / transform /
    # steering) OUTRANKS an old gold "leave this blank" suppression for the same
    # field. Without this, a gold file that left e.g. Delivery Method or D-U-N-S
    # empty would keep suppressing them even after the analyst mapped them. So drop
    # any suppression whose field also has a column mapping — the mapping wins.
    suppressed_targets -= set(by_target.keys())
    _suppressed_norm -= set(by_target_norm.keys())
    src_index: dict[str, str] = {}
    if conversion.dataset_id:
        cols = await DatasetColumnProfile.find(
            DatasetColumnProfile.dataset_id == conversion.dataset_id
        ).to_list()
        for c in cols:
            src_index[_normalize(c.column_name)] = c.column_name
    fields_map: dict = {}
    # Which SHEET each target field belongs to. Needed because a learning is
    # keyed by field NAME and Oracle repeats names across sheets, so without this
    # one approval reaches all 19 Customer sheets — see sheet_allowed.
    field_sheet: dict = {}
    if conversion.template_id:
        fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list()
        fields_map = {f.id: f.field_name for f in fields}
        sheet_names: dict = {}
        try:
            from app.models.fbdi import FBDISheet
            sheet_names = {sh.id: sh.sheet_name for sh in await FBDISheet.find(
                FBDISheet.template_id == conversion.template_id).to_list()}
        except Exception:                                       # noqa: BLE001
            sheet_names = {}
        for f in fields:
            field_sheet[f.id] = (getattr(f, "sheet_name", None)
                                 or sheet_names.get(getattr(f, "sheet_id", None)))
    auto_count = 0
    now = datetime.utcnow()
    for m in mappings:
        if not _eligible(m):
            continue
        tgt_name = fields_map.get(m.target_field_id)
        if not tgt_name:
            continue
        candidates = by_target.get(tgt_name) or by_target_norm.get(_normalize(tgt_name))
        if not candidates:
            continue
        for lm in candidates:
            # Respect the learning's sheet scope before anything else — an
            # excluded sheet must not even be considered a candidate.
            if not sheet_allowed(lm, field_sheet.get(m.target_field_id)):
                continue
            actual_src = src_index.get(_normalize(lm.original_value))
            if not actual_src:
                continue
            update = {
                "source_column": actual_src, "confidence": 1.0,
                "review_required": 0, "status": "approved",
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f'Auto-applied from learning library (captured from "{lm.captured_from}")',
            }
            if lm.rule_type:
                update["suggested_transformation"] = {
                    "rule_type": lm.rule_type, "config": lm.rule_config or {},
                    "description": "Re-applied from learned rule",
                }
            await m.set(update)
            await lm.set({"records_auto_fixed": (lm.records_auto_fixed or 0) + 1})
            auto_count += 1
            break

    # Suppression pass — fields the gold example left blank override the AI's
    # aggressive mapping: any still-"suggested" mapping for such a target is set
    # not_applicable so it stays empty at output.
    if suppressed_targets or _suppressed_norm:
        for m in mappings:
            if not _eligible(m):
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if tgt_name and (tgt_name in suppressed_targets
                             or _normalize(tgt_name) in _suppressed_norm):
                await m.set({
                    "source_column": None, "status": "not_applicable",
                    "review_required": 0, "approved_by": "learning-engine",
                    "approved_at": now,
                    "reason": "Suppressed — blank in the uploaded gold example",
                    "updated_at": now,
                })
                auto_count += 1

    # ── Mapping-document-only pass ────────────────────────────────────────────
    # When an analyst mapping document exists for this object, it is AUTHORITATIVE:
    # nothing may be sourced from a column the document does not sanction. The
    # deterministic matcher and the AI otherwise keep inventing plausible-looking
    # pairs that are simply wrong, and because a "suggested" mapping still exports
    # unless someone rejects it by hand, those guesses reach the FBDI. Observed on a
    # real eBOS vendor run: fax_num -> Account Currency Code, bank_code -> Fax Area
    # Code, country -> Fax Country Code, fax_num -> Customer Number, vend_num ->
    # ATTRIBUTE_NUMBER1. A fax number in a currency column fails the load outright.
    #
    # The allow-list is every (target field, source column) pair the learning
    # library holds for this object — i.e. the seeded analyst docs, transform rules
    # and gold-derived mappings. Anything still merely "suggested" whose pair is not
    # on that list is set not_applicable, so it exports blank instead of wrong.
    #
    # Deliberately untouched: mappings with no source column (constants and control
    # defaults such as Batch ID / Import Action), anything a human set
    # (overridden / approved / rejected), and objects with too few learnings to
    # constitute a document — a brand-new object with no doc must still map freely,
    # otherwise the tool would produce nothing at all for it.
    _MIN_DOC_ROWS = 5
    allowed_pairs: set[tuple[str, str]] = set()
    for lm in learned:
        if lm.target_field and lm.original_value:
            allowed_pairs.add((_normalize(lm.target_field), _normalize(lm.original_value)))
    if len(allowed_pairs) >= _MIN_DOC_ROWS:
        dropped = 0
        for m in mappings:
            if m.status != "suggested" or not m.source_column:
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if not tgt_name:
                continue
            if (_normalize(tgt_name), _normalize(m.source_column)) in allowed_pairs:
                continue
            await m.set({
                "source_column": None, "status": "not_applicable",
                "review_required": 0, "approved_by": "learning-engine",
                "approved_at": now,
                "reason": ("Not in the mapping document for this object — the analyst "
                           "mapping document is authoritative, so unsanctioned "
                           "source columns are left blank rather than guessed."),
                "updated_at": now,
            })
            dropped += 1
            auto_count += 1
        if dropped:
            logger.info("mapping-doc-only: dropped %d unsanctioned mappings for %s",
                        dropped, business_object)

    # Constant-default pass — re-apply the constant values learned from gold
    # (kind="example_default") so a brand-new conversion of this object inherits
    # them WITHOUT the user re-uploading the gold file. Only fills targets that
    # are still "suggested" (i.e. not covered by a learned column mapping above).
    defaults = await LearnedMapping.find({
        "kind": "example_default", "target_object": business_object, **_scope
    }).to_list()
    if defaults:
        # Latest instruction wins here too — first-come was letting an older gold
        # default hold a field the analyst had since re-specified.
        by_default: dict[str, LearnedMapping] = {}
        for lm in sorted(defaults, key=_effective_of, reverse=True):
            if lm.target_field:
                by_default.setdefault(lm.target_field, lm)
        # Same asterisk problem as the column pass: a default authored as
        # "Business Relationship" must still land on the template's
        # "Business Relationship*". Exact key first, normalized key as fallback.
        by_default_norm: dict[str, LearnedMapping] = {}
        _dclash: set[str] = set()
        for _tf, _lm in by_default.items():
            k = _normalize(_tf)
            if not k:
                continue
            if k in by_default_norm and by_default_norm[k] is not _lm:
                _dclash.add(k)
            by_default_norm.setdefault(k, _lm)
        for k in _dclash:
            by_default_norm.pop(k, None)
        for m in mappings:
            # A learned constant default is explicit intent to POPULATE the field,
            # so it also overrides a gold "not_applicable" suppression (not only
            # still-"suggested" targets) — e.g. an analyst default of Invoice Match
            # Option = Receipt must land even though the gold example left it blank.
            # Human "overridden"/"rejected" choices are still respected.
            if m.status in ("overridden", "rejected"):
                continue
            if not (_eligible(m) or m.status == "not_applicable"):
                continue
            tgt_name = fields_map.get(m.target_field_id)
            if not tgt_name:
                continue
            lm = by_default.get(tgt_name) or by_default_norm.get(_normalize(tgt_name))
            if lm is None:
                continue
            val = (lm.rule_config or {}).get("default_value")
            if val in (None, ""):
                val = lm.resolved_value
            if val in (None, ""):
                continue
            await m.set({
                "source_column": None, "default_value": val,
                "confidence": 0.96, "review_required": 0, "status": "approved",
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f'Constant default re-applied from learning library (from "{lm.captured_from}")',
                "updated_at": now,
            })
            await lm.set({"records_auto_fixed": (lm.records_auto_fixed or 0) + 1})
            auto_count += 1
    return auto_count


async def propagate_rules_to_downstream(
    source_conversion: Conversion, approved_mapping: MappingSuggestion
) -> list[dict]:
    rule = approved_mapping.suggested_transformation
    if not rule or not isinstance(rule, dict):
        return []
    rule_type = rule.get("rule_type")
    rule_config = rule.get("config", {})
    if not rule_type:
        return []
    tpl = await FBDITemplate.get(source_conversion.template_id) if source_conversion.template_id else None
    if not tpl:
        return []
    master_obj = tpl.business_object or source_conversion.target_object
    if not master_obj:
        return []
    key_names = REFERENCE_KEY_FIELDS.get(master_obj)
    if not key_names:
        return []
    src_fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
    source_field_name = next((f.field_name for f in src_fields if f.id == approved_mapping.target_field_id), None)
    if source_field_name not in key_names:
        return []
    siblings = await Conversion.find(
        Conversion.project_id == source_conversion.project_id,
        Conversion.id != source_conversion.id,
    ).to_list()
    propagated: list[dict] = []
    now = datetime.utcnow()
    for conv in siblings:
        if not conv.template_id:
            continue
        sib_fields = await FBDIField.find(FBDIField.template_id == conv.template_id).to_list()
        for f in sib_fields:
            if f.field_name not in key_names:
                continue
            existing = await TransformationRule.find_one({
                "conversion_id": conv.id, "target_field_id": f.id, "rule_type": rule_type
            })
            if existing:
                await existing.set({"rule_config": rule_config})
            else:
                await TransformationRule(
                    conversion_id=conv.id, target_field_id=f.id,
                    source_column=approved_mapping.source_column,
                    rule_type=rule_type, rule_config=rule_config,
                    description=f"Auto-propagated from {master_obj} master ({source_conversion.name})",
                    sequence=1,
                ).insert()
            propagated.append({
                "conversion_id": str(conv.id), "conversion_name": conv.name,
                "target_field": f.field_name, "rule_type": rule_type,
            })
    return propagated
