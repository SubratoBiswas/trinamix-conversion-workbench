"""Effective control-defaults for a conversion.

These are the values written at Generate Output for target FBDI fields that have
NO source column — standardization constants, not data pulled from the extract.
The mapping-review UI fetches this so it can show "defaulted -> value" instead of
a misleading red "required gap" for such fields.

Sources, in priority order per field:
  1. the conversion's own mapping.default_value (learned from a gold example)
  2. an example_default LearnedMapping captured for this target object (reusable)
  3. a running-key sequence field (output_service._SEQ_FIELDS)
  4. a static control constant (output_service._CONTROL_DEFAULTS)
  5. AI-inferred constant for a required field with no known default — only when
     AI_PROVIDER is configured; the result is cached as an example_default
     LearnedMapping so it's instant next time, consistent across engagements, and
     never re-billed. If AI is off or the call fails, the field simply stays a
     genuine required gap (deterministic behaviour, product never breaks).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import httpx

from app.config import settings
from app.models.conversion import Conversion
from app.models.fbdi import FBDIField
from app.models.learned import LearnedMapping
from app.models.mapping import MappingSuggestion
from app.services.output_service import _CONTROL_DEFAULTS, _SEQ_FIELDS

log = logging.getLogger(__name__)


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower().rstrip("*").strip()


async def _ai_infer_defaults(target_object: str, fields: list[dict]) -> dict[str, str]:
    """Ask the configured LLM for standard constant defaults for the given FBDI
    fields. Returns {normalized_field: value}. Empty dict when AI is disabled or
    on any error, so the deterministic path always keeps working."""
    provider = (settings.AI_PROVIDER or "none").lower()
    if provider not in ("anthropic", "openai") or not fields:
        return {}
    listing = "\n".join(
        f'- {f["label"]}' + (f' ({f["description"]})' if f.get("description") else "")
        for f in fields
    )
    prompt = (
        "You are an Oracle Fusion Cloud FBDI data-migration expert. For the "
        f"interface object '{target_object or 'this object'}', the target fields "
        "below have NO source column in the legacy extract. For EACH field, if "
        "Oracle expects a standard CONSTANT or enumerated default (an import "
        "action, a Y/N flag, a lookup code, an organization type, etc.), return "
        "that value. If the field genuinely needs per-row data (names, addresses, "
        "ids, amounts, dates, emails), OMIT it entirely. Return ONLY a JSON "
        "object mapping the exact field label to its constant value.\n\n"
        f"FIELDS:\n{listing}\n\n"
        'Example: {"Import Action *": "CREATE", "Federal Reportable Flag": "N"}'
    )
    try:
        if provider == "anthropic":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    # Latest Claude Sonnet for the constant-inference task.
                    # Honors an explicit ANTHROPIC_MODEL override, else the
                    # config default (claude-sonnet-4-6).
                    "model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                    "max_tokens": 1500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=45.0,
            )
            r.raise_for_status()
            data = r.json()
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )
        else:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.OPENAI_MODEL,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "You output strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=45.0,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        obj = json.loads(cleaned)
        if not isinstance(obj, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in obj.items():
            if v is None:
                continue
            out[_norm(str(k))] = str(v)
        return out
    except Exception as e:  # noqa: BLE001 -- never break the request over AI
        log.warning("AI default inference failed (%s); deterministic defaults only", e)
        return {}


async def _template_object(conversion) -> str | None:
    """The template's own business_object spelling, or None."""
    try:
        from app.models.fbdi import FBDITemplate
        tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
        return (getattr(tpl, "business_object", None) or None) if tpl else None
    except Exception:                                           # noqa: BLE001
        return None


async def compute_effective_defaults(conversion: Conversion, use_ai: bool = True) -> dict:
    """Return effective defaults for every unmapped target field of a conversion.

    Shape: {"defaults": {norm_field: value},
            "detail":   [{"field","label","value","source"}],
            "ai_used":  bool,
            "suppressed": [norm_field, …]}

    SUPPRESSION. Every field the analyst has said must ship blank is excluded here,
    at the top, before any source is consulted. This function is what the Mapping
    Review screen reads, so without that check the UI reported "Defaulted -> 900001"
    for a Batch ID that the corrections file, a suppress_field learning and a
    not_applicable mapping all agreed was blank — and the analyst had no way to make
    the number go away, because none of the three things they could press were read
    by the layer drawing the chip. The generated file was already blank; only the
    screen still said 900001, which is the worse of the two failures: it makes a
    correct fix look broken and invites re-fixing something that is not wrong.
    """
    if not conversion.template_id:
        return {"defaults": {}, "detail": [], "ai_used": False, "suppressed": []}

    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list()
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id
    ).to_list()
    by_fid = {m.target_field_id: m for m in maps}
    target_object = conversion.target_object or ""
    # ONE OBJECT KEY. A learning is WRITTEN under the template's business_object and
    # was READ here under the conversion's target_object with exact string equality —
    # so where those spellings differ the row is filed where nobody looks. The
    # generator uses the write key, so the value reached the FBDI file while being
    # invisible on this screen: a correct fix looking broken, which invites re-fixing
    # something that is not wrong.
    from app.services.learning_service import object_keys_for_object
    _obj_keys = sorted(set(object_keys_for_object(target_object)) |
                       set(object_keys_for_object(
                           (await _template_object(conversion)) or "")))

    # The same three sources output_service consults, resolved the same way, so the
    # screen and the file can no longer disagree about what is blank.
    suppressed: set[str] = set()
    try:
        from app.services.strategy_overlay import blank_fields as _strategy_blanks
        # Match the generator's object resolution (output_service.obj_name): the
        # template's business object first, since a conversion's target_object can
        # be the bundle's name rather than this sheet's.
        _obj = target_object
        try:
            from app.models.fbdi import FBDITemplate
            _tpl = await FBDITemplate.get(conversion.template_id)
            _obj = (getattr(_tpl, "business_object", None) or target_object)
        except Exception:  # noqa: BLE001
            pass
        suppressed |= {_norm(x) for x in _strategy_blanks(_obj)}
        suppressed |= {_norm(x) for x in _strategy_blanks(target_object)}
    except Exception:  # noqa: BLE001 — a missing overlay must not break defaults
        log.exception("strategy blank set unavailable while computing defaults")

    if target_object:
        try:
            from app.services.client_service import client_id_for_conversion, scope_query
            _sc = await scope_query(await client_id_for_conversion(conversion))
            async for lm in LearnedMapping.find(
                {"kind": "suppress_field", "target_object": {"$in": _obj_keys}},
                _sc,
            ):
                if lm.target_field:
                    suppressed.add(_norm(lm.target_field))
        except Exception:  # noqa: BLE001
            log.exception("suppress_field learnings unavailable while computing defaults")

    # A not_applicable mapping is the analyst pressing Keep blank. It only counts as
    # a suppression when it carries no explicit default — not_applicable WITH a
    # default means "populate with this constant" (e.g. Invoice Match Option =
    # Receipt), which is the same rule output_service.suppressed_keys applies.
    _f_by_id = {f.id: f for f in fields}
    for _m in maps:
        if (_m.status == "not_applicable"
                and not (_m.default_value and str(_m.default_value).strip())):
            _f = _f_by_id.get(_m.target_field_id)
            if _f and _f.field_name:
                suppressed.add(_norm(_f.field_name))

    # Reusable constants captured from gold examples for this object — scoped to
    # this conversion's client (+ global) so another client's defaults don't show.
    learned: dict[str, str] = {}
    # Per-field SHEET SCOPE, carried out to the caller. A default is keyed by field
    # NAME, and Oracle repeats field names across a multi-sheet interface — Customer
    # has 19 — so an unscoped default reaches every sheet that has a column of that
    # name. LearnedMapping.sheets / exclude_sheets and sheet_allowed already existed;
    # this function never consulted them, so the scope was recorded and ignored.
    # Analyst, 31-Jul: "Insert Update Indicator was set a default value as I and
    # approved. However it should only reflect in the RA_CUSTOMER_PROFILES_INT_ALL
    # sheet, where it is a mandatory field."
    scopes: dict[str, dict] = {}
    if target_object:
        from app.services.client_service import client_id_for_conversion, scope_query
        _scope = await scope_query(await client_id_for_conversion(conversion))
        # ORDERED. This was a plain loop with no sort, so the LAST row in Mongo
        # natural order won — and _upsert updates in place, so an edited (newer)
        # learning keeps its original early position and loses to any later-inserted
        # competitor. That is a concrete "I updated the default and the screen still
        # shows the old one". The engine ranks by effective date; so does this now,
        # applied oldest-first so the newest write lands last and wins.
        from app.services.learning_service import _effective_of
        _rows = await LearnedMapping.find(
            {"kind": "example_default", "target_object": {"$in": _obj_keys}},
            _scope,
        ).to_list()
        _rows.sort(key=_effective_of)
        for lm in _rows:
            if lm.target_field and lm.resolved_value:
                learned[_norm(lm.target_field)] = lm.resolved_value
                _only = [s for s in (getattr(lm, "sheets", None) or []) if str(s).strip()]
                _never = [s for s in (getattr(lm, "exclude_sheets", None) or []) if str(s).strip()]
                if _only or _never:
                    scopes[_norm(lm.target_field)] = {"sheets": _only,
                                                      "exclude_sheets": _never}

    defaults: dict[str, str] = {}
    detail: list[dict] = []
    ai_candidates: list[dict] = []

    for f in fields:
        m = by_fid.get(f.id)
        if m and m.source_column:
            continue  # mapped to a real source column -> not a default
        norm = _norm(f.field_name)
        # Blank means blank, whatever any lower layer still remembers. Checked
        # before m.default_value on purpose: a stale stored default left on the row
        # by an earlier seed is exactly what kept re-appearing after the analyst
        # blanked the field.
        if norm in suppressed:
            continue
        normc = norm.replace(" ", "")
        value: Optional[str] = None
        source = ""
        if m and m.default_value:
            value, source = m.default_value, "learned"
        elif norm in learned:
            value, source = learned[norm], "learned"
        elif normc in _SEQ_FIELDS:
            value, source = "auto-number (100000+)", "sequence"
        elif norm in _CONTROL_DEFAULTS:
            value, source = _CONTROL_DEFAULTS[norm], "control"

        if value is not None:
            defaults[norm] = value
            detail.append({"field": norm, "label": f.field_name, "value": value, "source": source})
        elif f.required:
            ai_candidates.append(
                {"label": f.field_name, "norm": norm, "description": (f.description or "")[:120]}
            )

    ai_used = False
    if ai_candidates and use_ai:
        ai_map = await _ai_infer_defaults(target_object, ai_candidates)
        for c in ai_candidates:
            v = ai_map.get(c["norm"])
            if not v:
                continue
            ai_used = True
            defaults[c["norm"]] = v
            detail.append({"field": c["norm"], "label": c["label"], "value": v, "source": "ai"})
            # Cache as reusable example_default (instant + consistent next time).
            # Look under EVERY spelling this object answers to, or the AI cache
            # writes a second row under the other one and the two then compete.
            exists = await LearnedMapping.find_one(
                {"kind": "example_default", "target_object": {"$in": _obj_keys},
                 "target_field": c["label"]},
                include_deleted=True,
            )
            # A retired default must stay retired — don't let the AI cache
            # re-create it on the next run (QA issue #5).
            if not exists:
                await LearnedMapping(
                    kind="example_default",
                    category="Default Value",
                    original_value="(ai)",
                    resolved_value=v,
                    target_object=target_object,
                    target_field=c["label"],
                    rule_type="default",
                    rule_config={"default_value": v},
                    captured_from="ai-inference",
                ).insert()

    # Returned so the UI can say "kept blank" rather than silently showing nothing —
    # an absent default and a deliberately blanked field look identical otherwise,
    # and the analyst needs to see that their decision took.
    return {"defaults": defaults, "detail": detail, "ai_used": ai_used,
            "suppressed": sorted(suppressed), "scopes": scopes}
