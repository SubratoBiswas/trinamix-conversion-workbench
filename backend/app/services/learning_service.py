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
from app.services import mapping_store

logger = logging.getLogger(__name__)


REFERENCE_KEY_FIELDS: dict[str, list[str]] = {
    "Item":     ["InventoryItemNumber", "Inventory Item Name", "Item Number", "ItemNumber"],
    "Customer": ["CustomerNumber", "Customer Number"],
    "Supplier": ["SupplierNumber", "Supplier Number"],
    "UOM":      ["UnitOfMeasureCode", "Unit of Measure Code"],
}


# One definition of the key form of a name, shared with the store. Two layers
# normalising the same string slightly differently is how a decision comes to be
# stored under a key nobody asks for.
_normalize = mapping_store.normalise_field


def _is_master_key_field(target_object: str | None, target_field: str | None) -> bool:
    if not target_object or not target_field:
        return False
    return target_field in REFERENCE_KEY_FIELDS.get(target_object, [])


async def _business_object_for(conversion: Conversion) -> str | None:
    """The object key a learning is filed under.

    This has to agree with whatever READS it, or a captured learning is stored under
    a key nobody asks for and is simply never seen again. Two consumers matter:
    ``defaults_service`` keys its example_default lookup on ``conversion.target_object``,
    and the Learning Center lists by the raw stored string with exact equality.

    The template's business_object is preferred because it is the precise interface
    ("Supplier Address" rather than the bundle's name) — but where the two disagree
    the conversion's own target_object is what the readers use, so it wins. Three
    bundled templates carry business_object="Supplier" while their conversions are
    "Supplier Import"/"Supplier Address", which is exactly the drift that made an
    eBOS Supplier Address default vanish from the Learning Center (CW #7): written
    under one spelling, asked for under the other.
    """
    _tgt = (getattr(conversion, "target_object", None) or "").strip()
    if conversion.template_id:
        tpl = await FBDITemplate.get(conversion.template_id)
        _bo = ((tpl.business_object if tpl else None) or "").strip()
        if _bo:
            # Same thing under two spellings → prefer the one the readers use.
            if _tgt and _normalize(_bo) != _normalize(_tgt):
                return _tgt
            return _bo
    return _tgt or None


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


# May a decision touch this interface sheet? One definition, in the store.
#
# Not a precedence scope — it does not create a tier that competes with the
# date. It is part of what the analyst SAID: Oracle repeats a field name across
# sheets (Customer has 19), and "id maps to Party Original System Reference, but
# not on HZ_IMP_CLASSIFICS_T" is one instruction, not two ranked ones.
sheet_allowed = mapping_store.sheet_allowed


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
    """Auto-capture after Generate Output. One more dated entry, nothing special.

    It used to refuse to "downgrade" a rule captured from a gold example, a
    prompt or an accepted crosswalk, on the grounds that those signals outranked
    it. That was authorship deciding precedence. It is now the date, like
    everything else: a decision made after the gold example is simply the later
    statement, and one made before it loses on its own merits — the store's
    "an older statement never overwrites a newer one" rule does the whole job.
    """
    if not business_object or not field_name:
        return False
    row = await _upsert(
        kind=kind,
        category=_category_for(rule_type) if kind == "column_mapping" else kind,
        original_value=str(original), resolved_value=str(resolved),
        target_object=business_object, target_field=field_name,
        rule_type=rule_type, rule_config=rule_config,
        captured_from=captured_from, captured_by=None,
        client_id=client_id, source_erp=source_erp,
    )
    return row is not None


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
    from app.services.mapping_dedupe import best_mapping_by_target
    best = best_mapping_by_target(maps)
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
            # mapping's confidence — capture it (issue #7). EXCEPT a generated
            # linkage/control value: Batch Identifier's per-conversion CONV-<id> and
            # the ORIG_SYSTEM keys are invented for THIS conversion, so capturing them
            # turns a one-conversion value into a client default that re-dates itself
            # on every generate and outranks a keep-blank (06-Aug: CONV-E3F9D5 kept
            # beating the approved suppression). Never learn those as reusable.
            from app.services.customer_structure_service import generated_role
            if generated_role(fname):
                continue
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


# Record a decision. The store's own adapter, kept under its old name because a
# lot of callers know it by that name; everything real happens in
# ``mapping_store.record_learning`` -> ``record_decision``.
_upsert = mapping_store.record_learning


async def bundle_objects_for(conversion) -> list[str]:
    """Every target object in this conversion's project — its load-sequence siblings.

    A Supplier load is SIX conversions with six different business objects — Import,
    Address, Site, Site Assignment, Contacts, Banks — shown as six tabs across the top
    of one screen. An analyst who corrects Supplier Name on the tab in front of them
    means all six, and said so: "I changed the mapping for supplier name in one of the
    conversions but it does not reflect in existing supplier conversions, it should
    affect everywhere and for future mappings as well" (31-Jul).

    An object-scoped fan-out reaches one sixth of that, and the five it misses look
    exactly like the five it reached, because nothing on any screen says which
    conversions an edit was meant to travel to.

    This used to live privately in steering_service — which is why only the steer box
    behaved this way while the mapping grid, where corrections are actually made, did
    not. Widening the fan-out does not widen what it writes: field-name matching, the
    source-column check and the date test still apply per conversion, so a sibling
    with no such field or no such column is skipped exactly as before.
    """
    try:
        pid = getattr(conversion, "project_id", None)
        if not pid:
            return []
        here = _normalize(await _business_object_for(conversion) or "")
        out, seen = [], {here}
        for c in await Conversion.find(Conversion.project_id == pid).to_list():
            o = (getattr(c, "target_object", None) or "").strip()
            if o and _normalize(o) not in seen:
                seen.add(_normalize(o))
                out.append(o)
        return out
    except Exception:                                           # noqa: BLE001
        return []


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
    _sheet_id = None
    if tpl:
        fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
        for f in fields:
            if f.id == mapping.target_field_id:
                target_field = f.field_name
                _sheet_id = getattr(f, "sheet_id", None)
                break
    if not target_field:
        return None
    # The interface sheet this decision was made ON. Oracle repeats field names
    # across sheets — Customer has 19 — so a name-keyed default captured from one
    # sheet was applied to all of them. Analyst, 31-Jul: "Insert Update Indicator
    # was set a default value as I and approved. However it should only reflect in
    # the RA_CUSTOMER_PROFILES_INT_ALL sheet, where it is a mandatory field."
    _sheet_name = None
    if _sheet_id is not None:
        try:
            from app.models.fbdi import FBDISheet
            _sh = await FBDISheet.get(_sheet_id)
            _sheet_name = getattr(_sh, "sheet_name", None) if _sh else None
        except Exception:  # noqa: BLE001 — scope is a refinement, never a blocker
            _sheet_name = None
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
        # A generated linkage/control field (Batch Identifier, the ORIG_SYSTEM keys)
        # holds a value the tool invents per conversion — it must never be captured as
        # a reusable default, or a per-conversion CONV-<id> becomes a client standard
        # that outranks the analyst's keep-blank (06-Aug Batch Identifier bug).
        from app.services.customer_structure_service import generated_role
        if generated_role(target_field):
            return None
        # Default-only decision → an example_default learning, so it shows up in
        # the Learning Centre's "Default values" tab immediately on save.
        return await _upsert(
            kind="example_default", category="Default Value",
            original_value="(default)", resolved_value=_default,
            target_object=business_object, target_field=target_field,
            rule_type="default", rule_config={"default_value": _default},
            project_id=conversion.project_id, client_id=_cid, source_erp=_src,
            captured_from=captured_from, captured_by=captured_by,
            # Scoped to the sheet it was set on. A multi-sheet interface repeats
            # field names, so an unscoped default reaches every sheet that happens
            # to have a column of the same name.
            sheets=[_sheet_name] if _sheet_name else None,
        )
    # AS A CLIENT RULE, which is what makes it reach everything. This used to
    # write one copy per sibling object in the load sequence — six writes to express
    # one decision, and still nothing for an object that does not exist yet. A single
    # CLIENT_RULE row says it once: this client maps this field from this column, in
    # every project and every conversion, existing or created next month.
    #
    # SHEET-SCOPED. Oracle repeats a field name across sheets (Customer has 19), so a
    # column mapping captured WITHOUT the sheet was applied to EVERY sheet with a
    # column of that name: an analyst who re-pointed one tab's column saw the change
    # spray across all tabs in the output AND — because apply_learned_to_conversion
    # re-writes each field's row from the store — back into the UI on the next open.
    # Reported as a major bug. The sheet is part of what the analyst said ("this
    # column, ON THIS TAB"), exactly as the default above already carries it. resolve()
    # evaluates the sheet per template, so a mapping still fans out to the SAME sheet in
    # every other conversion of the same object; it just no longer bleeds sideways onto
    # the sibling sheets. A field whose sheet cannot be resolved falls back to unscoped
    # (single-sheet interfaces, where there are no siblings to bleed onto).
    lm = await _upsert(
        kind="column_mapping", category="Column Mapping Alias",
        original_value=mapping.source_column, resolved_value=target_field,
        target_object=CLIENT_RULE, target_field=target_field,
        rule_type=rule_type, rule_config=rule_config,
        project_id=conversion.project_id, client_id=_cid, source_erp=_src,
        captured_from=captured_from, captured_by=captured_by,
        sheets=[_sheet_name] if _sheet_name else None,
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
    _sheet_id = None
    if rule.target_field_id:
        f = await FBDIField.get(rule.target_field_id)
        if f:
            target_field = f.field_name
            _sheet_id = getattr(f, "sheet_id", None)
    if not target_field:
        return None
    # The sheet this rule was authored ON — so a rule on a field Oracle repeats across
    # sheets (Customer has 19) does not spray to every sibling tab, the same major bug
    # the column-mapping capture above had.
    _sheet_name = None
    if _sheet_id is not None:
        try:
            from app.models.fbdi import FBDISheet
            _sh = await FBDISheet.get(_sheet_id)
            _sheet_name = getattr(_sh, "sheet_name", None) if _sh else None
        except Exception:  # noqa: BLE001 — scope is a refinement, never a blocker
            _sheet_name = None
    captured_from = f"{conversion.name} -- {target_field} (manual)"
    from app.services.client_service import client_id_for_conversion
    _cid = await client_id_for_conversion(conversion)
    # Learnings are keyed by source system too — see source_scope.
    _src = await source_erp_for_conversion(conversion)
    # The PROMPT the analyst typed to author this rule travels WITH it. It is stashed
    # under a reserved `_prompt` key in the stored config so it rides the same
    # propagation path as everything else — a rule that lands on a newer project's
    # mapping then still carries the sentence that explains it, which is what turns an
    # inherited derivation from "no rule saved" into "here is the rule, and why".
    # The engine ignores unknown config keys, so this changes nothing about execution.
    _cfg = dict(rule.rule_config or {})
    _prompt = (getattr(rule, "prompt", None) or getattr(rule, "description", None) or "").strip()
    if _prompt:
        _cfg["_prompt"] = _prompt
    # A CLIENT RULE, like every other decision the analyst makes by hand. The custom
    # transformation box is the SECOND plain-text place a rule gets written — the
    # analyst named both on 31-Jul, "one is the yellow global location, one is inside
    # custom transformation section for each column mapping" — and the two must not
    # behave differently. Object-scoped, this one reached the conversion it was typed
    # on and no other, which is the same complaint arriving through a different door.
    lm = await _upsert(
        kind="rule", category=_category_for(rule.rule_type),
        original_value=rule.source_column or "", resolved_value=target_field,
        target_object=CLIENT_RULE, target_field=target_field,
        rule_type=rule.rule_type, rule_config=_cfg,
        project_id=conversion.project_id, client_id=_cid, source_erp=_src,
        captured_from=captured_from, captured_by=captured_by,
        sheets=[_sheet_name] if _sheet_name else None,
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
    skip_origin: bool = True, extra_object_keys: list[str] | None = None,
) -> dict:
    """Push one analyst decision onto every conversion it applies to — by DATE.

    THE RULE, as the analyst stated it on 31-Jul: "the mapping file will provide the
    initial mapping, the gold standard the initial reference for output, but after
    conversion if the user wants to change any mapping, remove etc, the tool should
    apply it and update or include the learning center. THE LAST MAPPING WITH RESPECT
    TO DATE SHOULD BE CONSIDERED FINAL, and existing and new conversions should map
    and generate output according to that."

    That replaces what this function used to do. It used to skip ANY mapping a person
    had approved or overridden, forever — the reasoning being that a library entry
    must not overwrite a colleague's decision. The effect was that in a conversion the
    analyst had already worked through, which is most of them, a correction reached
    almost nothing; "apply to all existing conversions" was never what happened, and
    nothing said so.

    Now the comparison is a DATE, in both directions and for everyone:

      * a person's decision made AFTER this instruction stands — they spoke last;
      * a person's decision made BEFORE it is superseded — the instruction is the
        later statement of the same intent, which is exactly what "I changed my mind"
        means;
      * an engine-applied mapping never outranks anything.

    An undated decision is treated as OLDER than a dated instruction. A row with no
    approved_at cannot be shown to have come later, and the alternative — reading it
    as newer — is the behaviour that made corrections vanish.

    Affected outputs are marked stale rather than regenerated: the file on disk no
    longer matches the rules that would now run, and saying so is honest where
    regenerating dozens of files behind the analyst's back is not.
    """
    from app.models.fbdi import FBDIField, FBDISheet
    from app.services.client_service import explicit_client_id_for_conversion

    if lm is None or not lm.target_field:
        return {"conversions": 0, "mappings": 0, "stale_outputs": 0}
    business_object = await _business_object_for(origin)
    if not business_object:
        return {"conversions": 0, "mappings": 0, "stale_outputs": 0}
    cid = await explicit_client_id_for_conversion(origin)
    src = await source_erp_for_conversion(origin)
    as_of = _effective_of(lm)
    _keys = {_normalize(k) for k in object_keys_for_object(business_object)}
    # ACROSS THE BUNDLE. A Supplier load is SIX conversions — Import, Address, Site,
    # Site Assignment, Contacts, Banks — each a different business object, so an
    # object-scoped fan-out reaches one sixth of what the analyst calls "the output".
    # The steer box passes every object in the load sequence so a typed instruction
    # corrects the whole thing. Field-name matching, the source-column check and the
    # date test all still apply per conversion, so a sheet that has no such field or
    # no such column is skipped exactly as before.
    for _k in (extra_object_keys or []):
        _keys |= {_normalize(x) for x in object_keys_for_object(_k)}

    touched_convs = touched_maps = staled = skipped_newer = 0
    # WHY a conversion was passed over. Without this the payload said "conversions: 0"
    # and nothing else, and every diagnosis of "my change did not reach the others"
    # started from a screenshot. Nought because no template has the field, nought
    # because the column is missing from those extracts, and nought because a client
    # tag disagrees are three different problems with three different answers, and
    # they were indistinguishable.
    skipped: dict[str, int] = {}

    def _skip(reason: str):
        skipped[reason] = skipped.get(reason, 0) + 1

    now = datetime.utcnow()
    for conv in await Conversion.find_all().to_list():
        # The origin is skipped when the edit came from ITS screen — it is already
        # correct. When the edit came from the Learning Centre there is no origin in
        # that sense, only a conversion borrowed to resolve client/source scope, and
        # skipping it would leave one conversion silently un-updated.
        if (skip_origin and conv.id == origin.id) or not conv.template_id:
            continue
        # Object match on the NORMALISED key, and against every spelling this object
        # answers to. A bare != on the raw string made two conversions of the same
        # real object invisible to each other whenever the template's
        # business_object and the conversion's target_object disagreed by so much as
        # a space.
        # A CLIENT RULE belongs to the client, not to one object, so it is not
        # filtered by object at all — that is the whole point of it, and the reason
        # this no longer needs a bundle passed in from every call site. Everything
        # else still applies: the template must have the field, the extract must have
        # the column, the client and source system must match, and a later human
        # decision still wins.
        if lm.target_object is not None and (
                _normalize(await _business_object_for(conv)) not in _keys):
            _skip("different business object")
            continue
        # Client scope: a conversion nobody tagged is not another tenant's, so it is
        # in scope. This compares EXPLICIT tags only. It used to call
        # client_id_for_conversion, which falls back to the default client — so an
        # untagged project arrived here wearing the default tenant's id, compared
        # unequal to a tagged one, and was skipped as a cross-tenant leak. The
        # `cconv is not None` guard was written for exactly that case and could never
        # fire, because the fallback had already substituted an id.
        cconv = await explicit_client_id_for_conversion(conv)
        if cid is not None and cconv is not None and cconv != cid:
            _skip("different client")
            continue
        # Source-system scope, same rule the apply pass uses: a SyteLine conversion
        # must not inherit a NetSuite correction.
        csrc = await source_erp_for_conversion(conv)
        if src and csrc and csrc != src:
            _skip("different source system")
            continue
        fields = await FBDIField.find(FBDIField.template_id == conv.template_id).to_list()
        ids = {f.id for f in fields if _norm_field(f.field_name) == _norm_field(lm.target_field)}
        if not ids:
            _skip("template has no such field")
            continue
        # Sheet scope. record_learning_from_mapping writes sheets=[...] on a default
        # precisely so it stays on the sheet it was set on, and this loop matched by
        # field NAME across every sheet in the template — so propagation was strictly
        # wider than what the readers would then honour. Two layers, two answers.
        _sheet_of: dict = {}
        if getattr(lm, "sheets", None) or getattr(lm, "exclude_sheets", None):
            _sheets = {sh.id: sh.sheet_name for sh in await FBDISheet.find(
                FBDISheet.template_id == conv.template_id).to_list()}
            _sheet_of = {f.id: _sheets.get(getattr(f, "sheet_id", None)) for f in fields}
            ids = {i for i in ids if sheet_allowed(lm, _sheet_of.get(i))}
            if not ids:
                _skip("field is on a sheet this learning is scoped away from")
                continue
        # The source column this instruction names has to EXIST in this conversion's
        # extract, or propagation points a mapping at a column that is not there —
        # which reads as mapped on screen and produces nothing in the file.
        _src_ok = True
        if lm.kind == "column_mapping" and lm.original_value:
            _cols = await _dataset_columns_for(conv)
            if _cols is not None:
                _src_ok = _normalize(lm.original_value) in _cols
        if not _src_ok:
            _skip(f'no column "{lm.original_value}" in that conversion\'s extract')
            continue
        hit = False
        for m in await MappingSuggestion.find(
                MappingSuggestion.conversion_id == conv.id).to_list():
            if m.target_field_id not in ids:
                continue
            approver = (getattr(m, "approved_by", None) or "")
            _by_person = bool(approver) and approver != "learning-engine"
            if _by_person:
                _when = getattr(m, "approved_at", None)
                # Their word stands only while it is the LATER one.
                if _when is not None and as_of is not None and _when > as_of:
                    skipped_newer += 1
                    continue
            patch = {"status": "approved", "review_required": 0,
                     "approved_by": "learning-engine", "approved_at": now}
            if lm.kind == "column_mapping" and lm.original_value:
                patch["source_column"] = lm.original_value
            elif lm.kind == "example_default":
                patch["default_value"] = lm.resolved_value
            elif lm.kind == "suppress_field":
                # "Remove this mapping" is a decision like any other and had NO
                # branch here at all — a suppression set status=approved and left the
                # source column in place, i.e. it did the opposite of what it said.
                patch.update({"status": "not_applicable", "source_column": None,
                              "default_value": None,
                              "suggested_transformation": None})
            if lm.rule_type and lm.kind != "suppress_field":
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
    if skipped_newer:
        skipped["a later decision by a person"] = skipped_newer
    return {"conversions": touched_convs, "mappings": touched_maps,
            "stale_outputs": staled, "target_field": lm.target_field,
            "skipped_newer_decision": skipped_newer, "skipped": skipped,
            "as_of": as_of.isoformat() if as_of else None,
            "captured_by": captured_by}


async def _dataset_columns_for(conv) -> set | None:
    """Normalised column names of a conversion's bound sources, or None if unknown.

    None means "cannot tell", and the caller then does NOT filter — refusing to
    propagate because a profile is missing would be worse than the problem.
    """
    try:
        ids = [d for d in (getattr(conv, "source_dataset_ids", None) or []) if d]
        if not ids and getattr(conv, "dataset_id", None):
            ids = [conv.dataset_id]
        if not ids:
            return None
        profs = await DatasetColumnProfile.find({"dataset_id": {"$in": ids}}).to_list()
        if not profs:
            return None
        return {_normalize(p.column_name) for p in profs if p.column_name}
    except Exception:                                           # noqa: BLE001
        return None


# A learning whose target_object is None is a CLIENT RULE: it belongs to the client,
# not to one business object, and every object that has a field of that name answers
# to it.
#
# Analyst, 31-Jul, after several rounds of widening object-scoped fan-outs one call
# site at a time: "whatever user is saving or changing in mapping, store it or save it
# as mapping rule from client perspective (in this case NextPower), so it will
# correctly propagate through older projects and conversions and newer projects and
# conversions."
#
# That is a better model than the one it replaces, and not only simpler. An
# object-scoped decision has to be COPIED to each sibling object to reach the load
# sequence, and copied again for objects that do not exist yet — so "everywhere"
# was a growing list of writes, every one of which could be forgotten at a new call
# site, and was. A client rule is ONE row that every reader already has to look at.
# The scopes that carry real meaning are kept: the CLIENT (another tenant must never
# inherit) and the SOURCE SYSTEM (a SyteLine extract does not have NetSuite's
# columns). Both are properties of the data, not of how the work happens to be filed.
CLIENT_RULE = None


def object_keys_with_client_rules(business_object: str | None) -> list:
    """Read key set: this object's spellings PLUS client-level rules.

    Every reader of the library asks with this, so a client rule is visible to all of
    them without each one growing its own special case.
    """
    return [*object_keys_for_object(business_object), CLIENT_RULE]


def object_keys_for_object(business_object: str | None) -> list[str]:
    """Every spelling one business object answers to.

    A learning is WRITTEN under the template's business_object and READ, in the
    defaults layer and the Learning Centre, under the conversion's target_object.
    Where those differ the row is filed where nobody looks — and because the
    generator uses the write key, the value reaches the FBDI file while being
    invisible on screen, which is the worst version: it makes a correct fix look
    broken and invites re-fixing something that is not wrong.

    Rather than migrate the stored strings, every reader asks for all of them.
    """
    b = (business_object or "").strip()
    if not b:
        return []
    out = {b}
    for alt in (b.replace("_", " "), b.replace(" ", "_"), b.title(), b.upper(), b.lower()):
        if alt.strip():
            out.add(alt.strip())
    return sorted(out)


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


def _rule_columns_present(lm, src_index: dict) -> set[str]:
    """Which of the columns this stored rule reads does the extract actually have?

    Empty means the rule cannot produce anything here — either it names nothing or
    it names only columns this file does not carry — and applying it would put an
    approved-looking mapping on a field that ships blank.

    A rule that reads no source column at all but derives from a TARGET one (the
    Party Number sequence reads Party Type) still counts, because its key column
    is declared in the config and is checked the same way.
    """
    try:
        from app.services.output_service import _rule_referenced_columns
    except Exception:                                           # noqa: BLE001
        return set()
    cols = _rule_referenced_columns(
        [{"rule_type": lm.rule_type, "config": lm.rule_config or {}}])
    return {c for c in cols if _normalize(c) in src_index}


from app.services.bulk_write import BulkPatcher                      # noqa: E402


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
    def _eligible(m: MappingSuggestion, entry=None) -> bool:
        """May the store's answer be written onto this row?

        A person's decision is not permanently immune — it is the latest one that
        wins, which is the same rule everywhere else. This gate used to have no
        date test at all: a human approval blocked the library forever, so an
        analyst correction made in June out-ranked the mapping workbook they
        handed over in August, and nothing on screen said why.

        Their word still stands while it is the later one, and an approval with no
        timestamp counts as older, because it cannot be shown to have come after.
        """
        if m.status == "suggested":
            return True
        if not mapping_store.decided_by_a_person(m):
            # The engine's own copy. Refreshing it from the store is the point.
            return bool(force) or m.status != "approved"
        if entry is None:
            return False
        when = getattr(m, "approved_at", None)
        return not (when is not None and when >= (entry.effective_date or datetime.min))

    business_object = await _business_object_for(conversion)
    if not business_object:
        return 0
    # The legacy system this conversion reads FROM. Part of the key: the same
    # target field is fed by a different column depending on which system the
    # extract came from, so a SyteLine Item conversion must not inherit
    # NetSuite's Item mappings and point at columns its extract does not have.
    from app.services.client_service import client_id_for_conversion
    _src = await source_erp_for_conversion(conversion)

    # ONE read of the store, and the resolver decides. There used to be a query
    # per kind, each object-scoped by a $in over every spelling the object answers
    # to, and then three different orderings on top — a strong-transform sort for
    # columns, a date sort for defaults, and a hand-written rule that a column
    # mapping beats a suppression. Those were four precedence tiers competing with
    # the date, and they disagreed with the three other places that also ranked
    # these rows. There is one rule now: newest wins, and a suppression is simply
    # another statement about the same field.
    _cid = await client_id_for_conversion(conversion)
    _rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(mapping_store.DECISION_KINDS)}}).to_list()
    _entries = mapping_store.entries_of(_rows)
    if not _entries:
        return 0

    def _winner(field_name: str, sheet: str | None):
        return mapping_store.resolve(_entries, target_field=field_name,
                                     client_id=_cid, source_erp=_src, sheet=sheet)

    src_index: dict[str, str] = {}
    if conversion.dataset_id:
        cols = await DatasetColumnProfile.find(
            DatasetColumnProfile.dataset_id == conversion.dataset_id
        ).to_list()
        for c in cols:
            src_index[_normalize(c.column_name)] = c.column_name
    # A Customer load's columns live on DIFFERENT source files — person names in the
    # contact file, companyname/startdate/datecreated in the master — and the merge
    # JOINS them onto every source by entityid at GENERATE time (customer_merge
    # enrichment). So a derive/rule that reads firstname or startdate is legitimate
    # even when THIS conversion's own extract does not carry the column. Without this,
    # the gate below skips those bindings as "column not in the extract" and the fields
    # the analyst mapped (Person First/Middle/Last Name, the site From Dates) ship
    # blank — REC-05 / REC-07 / REC-08. Treat the cross-grain columns as available for
    # a Customer conversion; a column no source actually supplies just enriches to
    # blank, exactly as before.
    if "customer" in (business_object or "").lower():
        from app.services import customer_merge as _cm_enr
        for _bc in _cm_enr.BORROWABLE_SRC_COLS:
            src_index.setdefault(_normalize(_bc), _bc)
        # ``__source_sheet`` is the pseudo-column the generator injects to say WHICH
        # source file a row came from (Customer_Billing_Address vs Customer_Shipping_
        # Address). The 03-Aug document's BILL_TO/SHIP_TO and _B/_S rules read ONLY it,
        # so without treating it as available the gate below skips them as "column not
        # in the extract" and Part Site Use Type / Purpose / Site Use Code fall back to
        # an AI guess (all BILL_TO). It is always present at generate time for a
        # Customer conversion, so it is legitimately available here.
        src_index.setdefault(_normalize("__source_sheet"), "__source_sheet")
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

    # ── Every write in this pass goes out in ONE round trip ──────────────────
    #
    # This function writes one document per target field, and a 19-sheet Customer
    # conversion has well over a thousand of them. Sent one at a time, the cost is
    # the LATENCY, not the work: at 2ms to the database that is a couple of
    # seconds nobody notices, and at 250ms — an app and a cluster in different
    # regions, or an analyst on a slow link to a cold instance — it is four
    # minutes for exactly the same code.
    #
    # That is why the slowness reads as random: the same click is instant for one
    # person and a timeout for another, and the profiler on the fast machine
    # shows nothing wrong. Reported by the analysts working from India.
    #
    # The "only write what changed" rule below is UNCHANGED and still matters —
    # it is what keeps the queue short on a re-generate that resolves to what is
    # already stored. Batching does not replace it; it removes the cost of the
    # writes that do have to happen.
    bulk = BulkPatcher()
    counters: dict = {}

    def _write(m, patch: dict) -> bool:
        """Queue only what actually changes.

        A generate that resolves to exactly what the row already says should not
        touch the row at all — this is what let the resolve pass run on a
        19-sheet object without the hundreds of round-trips that used to make
        generation hang, and it is why the pass no longer has to be skipped
        there. Skipping it was the reason a heavy object could ship without ever
        consulting the library.

        No longer a coroutine: it applies the patch in memory immediately and
        puts it on the wire at the end. Callers still `await` it — awaiting a
        bool is a no-op — so this could not silently change a call site.
        """
        changed = {k: v for k, v in patch.items()
                   if k not in ("approved_at", "updated_at", "derived_at")
                   and getattr(m, k, None) != v}
        if not changed:
            return False
        bulk.set(m, patch)
        return True

    # What the store says about every field on this conversion, resolved once.
    decisions: dict = {}
    for m in mappings:
        tgt_name = fields_map.get(m.target_field_id)
        if tgt_name:
            decisions[m.target_field_id] = _winner(
                tgt_name, field_sheet.get(m.target_field_id))

    for m in mappings:
        entry = decisions.get(m.target_field_id)
        if entry is None or entry.decision not in (mapping_store.SOURCE_COLUMN,
                                                   mapping_store.RULE):
            continue
        if not _eligible(m, entry):
            continue
        tgt_name = fields_map.get(m.target_field_id)
        for lm in [entry.row]:
            actual_src = src_index.get(_normalize(entry.value))
            # A rule that reads OTHER columns needs no source column of its own.
            #
            # This is the seam that made every multi-column rule in the store
            # inert. The check below is right for a plain column mapping — naming
            # a column the extract does not have would point the row at nothing
            # and the screen would still read "approved" — but it was applied to
            # rules too, and a CONCAT, COALESCE, CASE_WHEN or SEQUENCE has no
            # single source column to name. Their stored `original_value` is the
            # literal "(rule)", which is in no extract, so EVERY one of them hit
            # this `continue` and never reached the mapping. The rules were
            # written, dated, visible in the Learning Centre, and did nothing —
            # the shipped-and-inert shape this codebase keeps repeating.
            #
            # A rule earns its way through by naming at least one column the
            # extract actually has, which is the same evidence the column-mapping
            # branch demands, asked of the rule's own config.
            rule_cols = _rule_columns_present(lm, src_index) if lm.rule_type else set()
            if not actual_src and not rule_cols:
                continue
            update = {
                "confidence": 1.0,
                "review_required": 0, "status": "approved",
                "approved_by": "learning-engine", "approved_at": now,
                "reason": f'Auto-applied from learning library (captured from "{lm.captured_from}")',
                "derived": True, "derived_at": now,
                "derived_from": entry.captured_from,
            }
            if actual_src:
                update["source_column"] = actual_src
            elif rule_cols:
                # Leave whatever column the row already has. Nulling it here would
                # throw away a person's binding to satisfy a rule that never
                # needed the field to have one.
                update["reason"] = (
                    f'Derived by a stored rule ({lm.rule_type}) reading '
                    f'{", ".join(sorted(rule_cols)[:4])} '
                    f'(captured from "{lm.captured_from}")')
            if lm.rule_type:
                update["suggested_transformation"] = {
                    "rule_type": lm.rule_type, "config": lm.rule_config or {},
                    "description": "Re-applied from learned rule",
                }
            if _write(m, update):
                counters[lm.id] = counters.get(lm.id, 0) + 1
                auto_count += 1
            break

    # Suppression pass — fields the gold example left blank override the AI's
    # aggressive mapping: any still-"suggested" mapping for such a target is set
    # not_applicable so it stays empty at output.
    for m in mappings:
        entry = decisions.get(m.target_field_id)
        if entry is None or entry.decision != mapping_store.SUPPRESS:
            continue
        if not _eligible(m, entry):
            continue
        if _write(m, {
            "source_column": None, "default_value": None, "status": "not_applicable",
            "review_required": 0, "approved_by": "learning-engine",
            "approved_at": now,
            "reason": f'Kept blank — latest decision (from "{entry.captured_from}")',
            "updated_at": now,
            "derived": True, "derived_at": now,
            "derived_from": entry.captured_from,
        }):
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
    for e in _entries:
        if e.decision in (mapping_store.SOURCE_COLUMN, mapping_store.RULE) and e.value:
            allowed_pairs.add((_normalize(e.target_field), _normalize(e.value)))
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
            bulk.set(m, {
                "source_column": None, "status": "not_applicable",
                "review_required": 0, "approved_by": "learning-engine",
                "approved_at": now,
                "reason": ("Not in the mapping document for this object — the analyst "
                           "mapping document is authoritative, so unsanctioned "
                           "source columns are left blank rather than guessed."),
                "updated_at": now,
                "derived": True, "derived_at": now,
                "derived_from": "the store's sanctioned source columns",
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
    for m in mappings:
        entry = decisions.get(m.target_field_id)
        if entry is None or entry.decision != mapping_store.DEFAULT_VALUE:
            continue
        # A constant is explicit intent to POPULATE the field, so it also takes a
        # field back from an older "keep blank" — that is just the later statement
        # winning, the same as everywhere else.
        if not (_eligible(m, entry) or m.status == "not_applicable"):
            continue
        val = entry.value
        if val in (None, ""):
            continue
        if _write(m, {
            "source_column": None, "default_value": val,
            "confidence": 0.96, "review_required": 0, "status": "approved",
            "approved_by": "learning-engine", "approved_at": now,
            "reason": f'Constant default re-applied from the store (from "{entry.captured_from}")',
            "updated_at": now,
            "derived": True, "derived_at": now,
            "derived_from": entry.captured_from,
        }):
            if entry.row is not None:
                counters[entry.row.id] = counters.get(entry.row.id, 0) + 1
            auto_count += 1

    # One write for every mapping row, one for every counter. Two round trips for
    # a pass that used to make one per field.
    await bulk.flush()
    if counters:
        from app.services.bulk_write import bulk_increment
        await bulk_increment(LearnedMapping, counters, "records_auto_fixed")
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
