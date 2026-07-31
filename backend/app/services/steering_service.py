"""Prompt-steering for a conversion's output.

Lets a user type plain instructions to override the tool's mapping/defaults,
e.g.:
    default Business Relationship to PROSPECTIVE
    set Tax Organization Type = Corporation
    map Supplier Name from VENDOR_NAME
    Federal reportable as N

Each directive is matched to a target field of the conversion's template (by a
normalized name) and written as an approved MappingSuggestion, so Generate
Output honours it. Deterministic and testable (no LLM call); richer natural-
language steering can be layered on top later.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from app.models.conversion import Conversion
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion

log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").strip().lower().strip("*"))


async def _learn(business_object, target_field, *, kind, original, resolved,
                 rule_type=None, client_id=None, objects=None):
    """Store a steering directive as a reusable, CLIENT-SCOPED learned rule so it
    applies to future conversions of the same object for this client (and never
    leaks to another client).

    ACROSS THE WHOLE LOAD SEQUENCE, not just the object the analyst happened to be
    looking at. This is the half of "affect existing AND future conversions" that the
    fan-out cannot cover. Propagation walks conversions that exist NOW; a conversion
    created tomorrow inherits nothing from it and instead asks the library, and
    `apply_learned_to_conversion` asks by ITS OWN business object. So a rule typed on
    the Supplier Import screen was stored under "Supplier" alone — today's other five
    supplier conversions were corrected by the fan-out, and next month's five were
    not. Same instruction, right for a week, silently wrong afterwards, and no screen
    would ever show the difference.

    One row per sibling object fixes that at the point the question is asked. A row
    written for an object whose template has no such field is inert: every reader
    matches the target field by name against that template first.
    """
    if not business_object or not target_field:
        return
    seen, targets = set(), []
    for obj in [business_object, *(objects or [])]:
        k = (obj or "").strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            targets.append(k)
    for obj in targets:
        # include_deleted=True: a retired steering rule is invisible to a plain
        # find_one, so the next prompt re-created it as a duplicate. CW #5.
        existing = await LearnedMapping.find_one(
            LearnedMapping.kind == kind,
            LearnedMapping.target_object == obj,
            LearnedMapping.target_field == target_field,
            LearnedMapping.client_id == client_id,
            include_deleted=True,
        )
        doc = {
            "kind": kind, "category": "Steering (prompt)",
            "original_value": str(original), "resolved_value": str(resolved),
            "target_object": obj, "target_field": target_field,
            "rule_type": rule_type, "client_id": client_id, "is_global": False,
            "captured_from": "prompt", "captured_at": datetime.utcnow(),
        }
        if existing:
            # Typing the directive is an explicit user action, so it revives in place.
            if getattr(existing, "is_deleted", False):
                doc = {**doc, "is_deleted": False,
                       "deleted_at": None, "deleted_by": None}
            await existing.set(doc)
        else:
            await LearnedMapping(**doc).insert()


# "clear X" / "blank X" / "leave X blank" / "don't map X" / "do not populate X"
# / "remove X"  → suppress (keep the field empty, overriding AI mapping)
_SUPPRESS_RES = [
    re.compile(r"^\s*(?:clear|blank|empty|remove|skip)\s+(?P<f>.+?)\s*$", re.I),
    re.compile(r"^\s*(?:don'?t|do not)\s+(?:map|populate|fill|use)\s+(?P<f>.+?)\s*$", re.I),
    re.compile(r"^\s*leave\s+(?P<f>.+?)\s+(?:blank|empty)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+(?:should be|must be)\s+(?:blank|empty)\s*$", re.I),
]
# "map X from Y" / "X should be mapped to Y" / "X from source Y"  (source column)
#
# ORDER MATTERS, and getting it wrong was destructive rather than merely useless.
# "supplier name should be mapped to legal name" was caught by the DEFAULT pattern
# `(?P<f>.+?) should be (?P<v>.+?)`, which read the field as "supplier name" and the
# CONSTANT as the literal string "mapped to legal name for all conversions" — and
# then wrote that sentence into the column on every row. A mapping instruction has
# to be recognised as a mapping BEFORE anything is allowed to read it as a default,
# so the map patterns are tried first and they explicitly cover "mapped to/from".
_MAP_RES = [
    re.compile(r"^\s*(?:map|pull|take)\s+(?P<f>.+?)\s+from\s+(?P<c>.+?)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+(?:should\s+be\s+|must\s+be\s+|is\s+)?"
               r"mapped\s+(?:to|from|with|using)\s+(?P<c>.+?)\s*$", re.I),
    re.compile(r"^\s*(?:map|set)\s+(?P<f>.+?)\s+(?:to|=)\s+(?:column|source)\s+(?P<c>.+?)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+from\s+(?:source\s+)?(?P<c>.+?)\s*$", re.I),
]
# "default X to Y" / "set X = Y" / "X as Y"  (default value)
_DEFAULT_RES = [
    re.compile(r"^\s*(?:default|set|make)\s+(?P<f>.+?)\s+(?:to|=|as)\s+(?P<v>.+?)\s*$", re.I),
    re.compile(r"^\s*(?P<f>.+?)\s+(?:should be|defaults? to)\s+(?P<v>.+?)\s*$", re.I),
]

# Trailing scope phrases the analyst adds by habit. They are not part of the column
# name — "legal name for all conversions" is the column "legal name" — and leaving
# them on produced a source column that matches nothing.
_SCOPE_TAIL = re.compile(
    r"\s*(?:,\s*)?\b(?:for|in|across|on)\s+(?:all|every|each)\s+"
    r"(?:the\s+)?(?:conversions?|sheets?|files?|objects?|records?|rows?)\s*\.?\s*$", re.I)


def _strip_scope(text: str) -> str:
    prev = None
    out = (text or "").strip()
    while prev != out:
        prev = out
        out = _SCOPE_TAIL.sub("", out).strip().rstrip(",.")
    return out


async def _upsert(conversion, field, *, source_column, default_value, reason,
                  business_object=None, client_id=None, actor=None, objects=None):
    existing = await MappingSuggestion.find_one(
        MappingSuggestion.conversion_id == conversion.id,
        MappingSuggestion.target_field_id == field.id,
    )
    payload = {
        "source_column": source_column,
        "default_value": default_value,
        "confidence": 1.0,
        "reason": reason,
        "status": "approved",
        "review_required": 0,
        "updated_at": datetime.utcnow(),
        # A steering instruction is the ANALYST deciding, so it is stamped like one.
        # These rows used to be written status=approved with NO approver and NO date,
        # which under "the last decision by date is final" made them unrankable: they
        # could never be shown to be recent, so any dated rule beat them.
        "approved_by": actor or "steering-prompt",
        "approved_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(payload)
    else:
        await MappingSuggestion(conversion_id=conversion.id, target_field_id=field.id, **payload).insert()
    # Persist as a reusable client-scoped rule.
    if source_column:
        await _learn(business_object, field.field_name, kind="column_mapping",
                     original=source_column, resolved=field.field_name,
                     client_id=client_id, objects=objects)
    elif default_value is not None:
        await _learn(business_object, field.field_name, kind="example_default",
                     original="(default)", resolved=default_value, rule_type="default",
                     client_id=client_id, objects=objects)


async def _suppress(conversion, field, business_object, client_id=None, actor=None,
                    objects=None):
    """Force a field to stay blank (status not_applicable), overriding AI mapping,
    and learn it as a reusable client-scoped suppression for this object."""
    existing = await MappingSuggestion.find_one(
        MappingSuggestion.conversion_id == conversion.id,
        MappingSuggestion.target_field_id == field.id,
    )
    payload = {
        "source_column": None, "default_value": None, "confidence": 1.0,
        "reason": "prompt: leave blank", "status": "not_applicable",
        "review_required": 0, "updated_at": datetime.utcnow(),
        "approved_by": actor or "steering-prompt",
        "approved_at": datetime.utcnow(),
    }
    if existing:
        await existing.set(payload)
    else:
        await MappingSuggestion(conversion_id=conversion.id, target_field_id=field.id, **payload).insert()
    await _learn(business_object, field.field_name, kind="suppress_field",
                 original="(blank)", resolved="", rule_type="suppress",
                 client_id=client_id, objects=objects)


async def _ai_parse_directives(lines: list[str], field_names: list[str],
                               source_names: list[str] | None = None) -> list[dict]:
    """Turn free-form English steering into structured directives, with the model.

    THE MODEL GOES FIRST. It used to be a fallback for whatever the regexes could not
    place, which is backwards: the regexes are a small fixed set of shapes and they
    fail SILENTLY AND DESTRUCTIVELY outside them. "supplier name should be mapped to
    legal name" matched the DEFAULT pattern and wrote that whole sentence into the
    column as a constant. A model reads it correctly, and the regexes are the
    fallback for when there is no API key.

    It is also given the real SOURCE COLUMN list. Without it the model had to guess
    the spelling of the column the analyst meant — "legal name" against a file whose
    header is `Legal Name` or `legalname` — and a guessed column binds to nothing.

    Returns {action: map|default|suppress, field, value?, source?}. Best-effort: on
    any error (no key, network, bad JSON) returns [] so steering still works offline.
    """
    from app.config import settings
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key or not lines:
        return []
    import httpx
    fields_blob = "\n".join(f"- {n}" for n in field_names[:400])
    sources_blob = "\n".join(f"- {n}" for n in (source_names or [])[:400])
    prompt = (
        "You turn plain-English data-migration instructions into structured "
        "directives, against a fixed list of Oracle target fields and a fixed list "
        "of the legacy source columns actually present in this file.\n\n"
        "TARGET FIELDS (Oracle side):\n" + fields_blob + "\n\n"
        + ("SOURCE COLUMNS (legacy file):\n" + sources_blob + "\n\n"
           if sources_blob else "")
        + "INSTRUCTIONS (one per line):\n" + "\n".join(f"- {l}" for l in lines) + "\n\n"
        "For each instruction you can confidently act on, output one JSON object:\n"
        '  "action": "map" | "default" | "suppress",\n'
        '  "field":  the EXACT target field name, copied from the TARGET FIELDS list,\n'
        '  "source": for action=map, the EXACT source column, copied from the SOURCE\n'
        "            COLUMNS list (never invent one; omit the directive if no listed\n"
        "            column is clearly the one meant),\n"
        '  "value":  for action=default, the constant to write.\n\n'
        "Rules:\n"
        "- 'X should be mapped to Y', 'map X from Y', 'X comes from Y' are all "
        "action=map, where X is the TARGET field and Y the SOURCE column. Do NOT "
        "read these as a constant.\n"
        "- action=default is only for a literal constant value ('set Import Action "
        "to CREATE'), never for a sentence describing a mapping.\n"
        "- Use 'suppress' when the field should be left blank / not populated.\n"
        "- Ignore scope phrases like 'for all conversions' — they are not part of "
        "any column name.\n"
        "- Only include instructions you can match to a listed field.\n"
        'Respond with ONLY a JSON array, e.g. [{"action":"map","field":"Supplier Name","source":"Legal Name"}].'
    )
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": getattr(settings, "ANTHROPIC_MODEL", None) or "claude-sonnet-4-6",
                      "max_tokens": 1500,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")
        s, e = text.find("["), text.rfind("]")
        if s == -1 or e == -1:
            return []
        out = json.loads(text[s:e + 1])
        return [d for d in out if isinstance(d, dict) and d.get("action") and d.get("field")]
    except Exception as exc:  # noqa: BLE001
        log.warning("steering AI parse failed: %s", exc)
        return []


async def apply_steer_prompt(conversion: Conversion, prompt: str,
                             actor: str | None = None) -> dict:
    """Turn typed English into mappings, apply them, learn them, and push them out.

    Analyst, 31-Jul: "If I add a rule here, it should be converted from English to a
    rule and applied in the mappings, stored in learning, applied to all previous
    (existing) conversions and future conversions too" — and "all should be done
    using AI, or a Python function whichever is available."

    So: THE MODEL PARSES FIRST and the deterministic parser is the fallback, not the
    other way round. That ordering is not a preference. The regexes are a small fixed
    set of shapes that fail SILENTLY AND DESTRUCTIVELY outside them: "supplier name
    should be mapped to legal name for all conversions" matched
    `(?P<f>.+?) should be (?P<v>.+?)` and wrote the sentence
    "mapped to legal name for all conversions" into the column as a CONSTANT, on
    every row. Both parsers are fixed; the model simply reads it right.

    Four things happen to every directive, which is the whole of the request:
      1. applied to THIS conversion's mapping row, stamped and dated as an analyst
         decision so it can be ranked;
      2. stored in the learning library, so future conversions inherit it;
      3. PROPAGATED to conversions that already exist — this is what was missing, and
         it is the half the analyst asked about by name;
      4. reported, with counts, including anything that could not be resolved.
    """
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    if not template:
        return {"applied": [], "unmatched": [], "error": "no template"}
    business_object = template.business_object or conversion.target_object
    from app.services.client_service import client_id_for_conversion
    client_id = await client_id_for_conversion(conversion)
    # The load-sequence siblings, computed ONCE and used for both halves of "change
    # everywhere": the library rows written below (which is what a conversion created
    # next month will read) and the fan-out at the end (which is what conversions that
    # already exist get). Computing it only for the fan-out, as this did, covered the
    # existing six and silently missed every future one.
    bundle = await _bundle_objects(conversion, business_object)
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
    by_key = {}
    for f in fields:
        by_key.setdefault(_norm(f.field_name), f)

    def find_field(name: str):
        k = _norm(name)
        if k in by_key:
            return by_key[k]
        for kk, f in by_key.items():            # loose contains as a fallback
            if k and (k in kk or kk in k):
                return f
        return None

    # The REAL source columns. Both parsers resolve against these, so a column the
    # analyst half-remembers ("legal name") binds to the one the file actually has
    # ("Legal Name") — and a name that matches nothing is REPORTED rather than
    # written onto the mapping, where it would read as mapped and produce nothing.
    src_names = await _source_columns(conversion)
    src_by_key = {_norm(c): c for c in src_names}

    def find_source(name: str):
        k = _norm(_strip_scope(name))
        if k in src_by_key:
            return src_by_key[k]
        hits = [c for kk, c in src_by_key.items() if k and (k in kk or kk in k)]
        return hits[0] if len(hits) == 1 else None

    applied, unmatched, unresolved = [], [], []
    lines = [l.strip().rstrip(".") for l in re.split(r"[\n;]+", prompt or "") if l.strip()]

    async def _do_map(f, col_raw, via):
        col = find_source(col_raw)
        if not col:
            unresolved.append({"field": f.field_name, "wanted_source": _strip_scope(col_raw),
                               "reason": "no column by that name in this file", "via": via})
            return None
        await _upsert(conversion, f, source_column=col, default_value=None,
                      reason=f"prompt ({via}): map", business_object=business_object,
                      client_id=client_id, actor=actor, objects=bundle)
        applied.append({"field": f.field_name, "source": col, "via": via})
        return ("column_mapping", f.field_name)

    async def _do_default(f, val, via):
        await _upsert(conversion, f, source_column=None, default_value=str(val),
                      reason=f"prompt ({via}): default", business_object=business_object,
                      client_id=client_id, actor=actor, objects=bundle)
        applied.append({"field": f.field_name, "default": str(val), "via": via})
        return ("example_default", f.field_name)

    async def _do_suppress(f, via):
        await _suppress(conversion, f, business_object, client_id, actor=actor,
                        objects=bundle)
        applied.append({"field": f.field_name, "suppressed": True, "via": via})
        return ("suppress_field", f.field_name)

    touched: list[tuple] = []

    # ── 1. THE MODEL, on everything ─────────────────────────────────────────
    ai_used = False
    handled_idx: set[int] = set()
    directives = await _ai_parse_directives(lines, [f.field_name for f in fields], src_names)
    if directives:
        ai_used = True
        for d in directives:
            f = find_field(str(d.get("field", "")))
            if not f:
                continue
            action = str(d.get("action", "")).lower()
            res = None
            if action == "suppress":
                res = await _do_suppress(f, "ai")
            elif action == "map" and d.get("source"):
                res = await _do_map(f, str(d["source"]), "ai")
            elif action == "default" and d.get("value") is not None:
                res = await _do_default(f, d["value"], "ai")
            if res:
                touched.append(res)
            # Mark the line this directive came from as handled.
            fk = _norm(f.field_name)
            for i, l in enumerate(lines):
                if i not in handled_idx and fk and fk[:8] in _norm(l):
                    handled_idx.add(i)
                    break

    # ── 2. the deterministic parser, on whatever is left ────────────────────
    for i, line in enumerate(lines):
        if i in handled_idx:
            continue
        res = None
        for rx in _SUPPRESS_RES:                 # blank first — "leave X blank" is
            m = rx.match(line)                   # not "default X to blank"
            if m:
                f = find_field(m.group("f"))
                if f:
                    res = await _do_suppress(f, "rule")
                break
        if res is None:
            for rx in _MAP_RES:                  # MAP before DEFAULT — see _MAP_RES
                m = rx.match(line)
                if m:
                    f = find_field(m.group("f"))
                    if f:
                        res = await _do_map(f, m.group("c"), "rule")
                    break
        if res is None:
            for rx in _DEFAULT_RES:
                m = rx.match(line)
                if m:
                    f = find_field(m.group("f"))
                    if f:
                        res = await _do_default(f, m.group("v").strip().strip('"\''),
                                                "rule")
                    break
        if res:
            touched.append(res)
        elif not any(u["field"] and _norm(u["field"])[:8] in _norm(line)
                     for u in unresolved):
            unmatched.append(line)

    # ── 3. out to the conversions that already exist ────────────────────────
    # Capturing the learning only ever covered FUTURE conversions; the ones already
    # open kept whatever they had. "Apply to all previous conversions" is this step,
    # and it was missing entirely from the steering path.
    fanout = {"conversions": 0, "mappings": 0, "stale_outputs": 0}
    # WHY conversions were passed over, merged across every directive. The loop below
    # summed only the keys already in `fanout`, so the reasons the fan-out now returns
    # were computed and then dropped one line before the caller — the exact shape this
    # codebase keeps producing: a fact recorded everywhere and read nowhere.
    fan_skipped: dict = {}
    if touched:
        try:
            from app.models.learned import LearnedMapping
            from app.services.learning_service import (
                object_keys_for_object, propagate_learning_to_open_conversions)
            keys = object_keys_for_object(business_object)
            # `bundle` is already computed above — it is the same list that decided
            # which library rows to write, and the two must not be allowed to drift.
            for kind, field in touched:
                lm = await LearnedMapping.find_one(
                    {"kind": kind, "target_object": {"$in": keys}, "target_field": field})
                if lm is None:
                    continue
                r = await propagate_learning_to_open_conversions(
                    lm, conversion, captured_by=actor or "steering-prompt",
                    extra_object_keys=bundle)
                for k in ("conversions", "mappings", "stale_outputs"):
                    fanout[k] += int(r.get(k, 0) or 0)
                for _why, _n in (r.get("skipped") or {}).items():
                    fan_skipped[_why] = fan_skipped.get(_why, 0) + int(_n or 0)
        except Exception as exc:  # noqa: BLE001 — never fail the steer on the fan-out
            log.exception("steering: propagating to existing conversions failed")
            fanout["error"] = f"{type(exc).__name__}: {exc}"[:200]
    if fan_skipped:
        fanout["skipped"] = fan_skipped

    return {"applied": applied, "unmatched": unmatched, "unresolved": unresolved,
            "ai_used": ai_used, "propagated": fanout,
            "parsed_by": "ai" if ai_used else ("rule" if applied else "none")}


async def _bundle_objects(conversion, business_object: str | None) -> list[str]:
    """Every target object in this conversion's project — its load-sequence siblings.

    Now a thin call through to ``learning_service.bundle_objects_for``. It lived here,
    privately, which is exactly why the steer box fanned out across all six supplier
    conversions and the mapping grid — where corrections are actually made — fanned out
    to one. Two answers to "which conversions does this reach?" in one application is
    the bug, not the duplication.
    """
    from app.services.learning_service import bundle_objects_for
    return await bundle_objects_for(conversion)


async def _source_columns(conversion) -> list[str]:
    """Every source column bound to this conversion, across all its sheets."""
    try:
        from app.models.dataset import DatasetColumnProfile
        ids = [d for d in (getattr(conversion, "source_dataset_ids", None) or []) if d]
        if not ids and getattr(conversion, "dataset_id", None):
            ids = [conversion.dataset_id]
        if not ids:
            return []
        profs = await DatasetColumnProfile.find({"dataset_id": {"$in": ids}}).to_list()
        out, seen = [], set()
        for p in profs:
            n = (p.column_name or "").strip()
            if n and _norm(n) not in seen:
                seen.add(_norm(n))
                out.append(n)
        return out
    except Exception:                                           # noqa: BLE001
        return []
