"""Data-quality rule store: load, extract-from-template, upload, apply.

Rules are scoped by (target_object, client) like learnings — a client's own rows
plus any global row. Three creation sources: EXTRACTED (derived from an FBDI
template's field metadata), UPLOADED (rules workbook/JSON), MANUAL. They feed the
Generate-time DQ step (generate_dq) and are also runnable on demand.
"""
from __future__ import annotations

import logging
from typing import Optional

from beanie import PydanticObjectId

from app.models.dq_rule import DataQualityRule
from app.models.fbdi import FBDIField, FBDITemplate

logger = logging.getLogger(__name__)

_VALID_TYPES = {"REQUIRED", "MAX_LENGTH", "VALUE_IN_SET", "REGEX", "NUMERIC", "NOT_NEGATIVE"}
_CLEANSE_TYPES = {"TRIM", "UPPERCASE", "LOWERCASE", "TITLECASE", "REMOVE_SPECIAL",
                  "REPLACE", "DEFAULT_IF_BLANK", "PAD_LEFT"}


def _rule_to_dict(r: DataQualityRule) -> dict:
    return {"id": str(r.id), "field": r.field, "rule_type": r.rule_type,
            "params": r.params or {}, "severity": r.severity, "kind": r.kind,
            "source": r.source, "active": r.active, "description": r.description}


async def load_rules(target_object: str, client_id: Optional[PydanticObjectId], kind: str,
                     include_extracted: bool = False) -> list[dict]:
    """Active rules for (object, client) of a given kind. For validation, extracted
    rules are excluded by default (they mirror the built-in FBDI checks that already
    run) so they aren't double-counted; uploaded/manual rules always apply. Cleansing
    rules always apply regardless of source."""
    from app.services.client_service import scope_query
    scope = await scope_query(client_id)
    q = {"kind": kind, "target_object": target_object, "active": True, **scope}
    rows = await DataQualityRule.find(q).to_list()
    out = []
    for r in rows:
        if kind == "validation" and r.source == "extracted" and not include_extracted:
            continue
        out.append(_rule_to_dict(r))
    return out


async def extract_rules_from_template(target_object: str, template_id, client_id: Optional[PydanticObjectId],
                                      created_by: str = "system") -> dict:
    """Derive validation rules from an FBDI template's field metadata and persist
    them as source='extracted' (editable). Idempotent on
    (kind, target_object, field, rule_type, client_id)."""
    tpl = await FBDITemplate.get(PydanticObjectId(str(template_id)))
    if not tpl:
        return {"error": "Template not found", "created": 0}
    fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
    created = skipped = 0
    for f in fields:
        specs: list[tuple[str, dict, str, str]] = []  # (rule_type, params, severity, desc)
        if f.required:
            specs.append(("REQUIRED", {}, "error", f"'{f.field_name}' is required."))
        if f.max_length:
            specs.append(("MAX_LENGTH", {"max_length": int(f.max_length)}, "error",
                          f"'{f.field_name}' max length {f.max_length}."))
        vals = [str(v.get("code") or v.get("value") or "").strip()
                for v in (f.allowed_values or []) if (v.get("code") or v.get("value"))]
        vals = [v for v in vals if v]
        if vals:
            specs.append(("VALUE_IN_SET", {"values": vals}, "error",
                          f"'{f.field_name}' must be one of {len(vals)} allowed codes."))
        dt = (f.data_type or "").lower()
        if dt in ("number", "numeric", "integer", "decimal"):
            specs.append(("NUMERIC", {}, "warning", f"'{f.field_name}' must be numeric."))
        for rule_type, params, severity, desc in specs:
            existing = await DataQualityRule.find(
                DataQualityRule.kind == "validation",
                DataQualityRule.target_object == target_object,
                DataQualityRule.field == f.field_name,
                DataQualityRule.rule_type == rule_type,
                DataQualityRule.client_id == client_id,
            ).first_or_none()
            if existing:
                skipped += 1
                continue
            await DataQualityRule(
                kind="validation", target_object=target_object, field=f.field_name,
                rule_type=rule_type, params=params, severity=severity, description=desc,
                source="extracted", client_id=client_id, created_by=created_by,
            ).insert()
            created += 1
    return {"target_object": target_object, "created": created, "skipped": skipped}


def _deterministic_proposals(fields: list) -> list[dict]:
    """Fallback proposals (no AI): required/max-length/value-set/numeric from
    metadata + a universal TRIM cleanse for text fields."""
    out: list[dict] = []
    for f in fields:
        if f.required:
            out.append({"kind": "validation", "field": f.field_name, "rule_type": "REQUIRED",
                        "params": {}, "severity": "error", "description": f"{f.field_name} is required."})
        if f.max_length:
            out.append({"kind": "validation", "field": f.field_name, "rule_type": "MAX_LENGTH",
                        "params": {"max_length": int(f.max_length)}, "severity": "error",
                        "description": f"Max length {f.max_length}."})
        vals = [str(v.get("code") or v.get("value") or "").strip()
                for v in (f.allowed_values or []) if (v.get("code") or v.get("value"))]
        vals = [v for v in vals if v]
        if vals:
            out.append({"kind": "validation", "field": f.field_name, "rule_type": "VALUE_IN_SET",
                        "params": {"values": vals}, "severity": "error",
                        "description": f"One of {len(vals)} allowed codes."})
    return out


async def ai_propose_rules(target_object: str, template_id, client_id, sample_dataset_id=None) -> dict:
    """Ask Claude to PROPOSE validation + cleansing rules from the FBDI field
    metadata (and, if given, a small sample of the client's data). Returns
    proposals for REVIEW — nothing is saved. Falls back to deterministic proposals
    if no AI key or on any error, so the button always returns something useful."""
    tpl = await FBDITemplate.get(PydanticObjectId(str(template_id)))
    if not tpl:
        return {"error": "Template not found", "proposals": []}
    fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()

    # Sample a few real values per column (best-effort) to ground the model.
    samples: dict[str, list[str]] = {}
    if sample_dataset_id:
        try:
            from app.models.dataset import Dataset
            from app.services.dataset_file_store import materialize_dataset_file
            from app.parsers import parse_tabular
            ds = await Dataset.get(PydanticObjectId(str(sample_dataset_id)))
            path = await materialize_dataset_file(ds) if ds else None
            if path:
                import pandas as pd
                sdf = parse_tabular(str(path), file_type=ds.file_type, nrows=200)
                for c in sdf.columns[:80]:
                    vals = [str(v).strip() for v in sdf[c].dropna().unique()[:5] if str(v).strip()]
                    if vals:
                        samples[str(c)] = vals
        except Exception:  # noqa: BLE001 — sampling is best-effort
            pass

    # Compact field catalog (cap to bound tokens).
    cat = []
    for f in fields[:120]:
        cat.append({"field": f.field_name, "required": bool(f.required),
                    "type": f.data_type, "max_length": f.max_length,
                    "lov": len(f.allowed_values or []) or None,
                    "samples": samples.get(f.field_name)})

    from app.config import settings
    from app.services.ai_settings import get_active_model
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    model = get_active_model()
    proposals: list[dict] = []
    ai_used = False
    dbg: dict = {"has_key": bool(api_key), "model": model}
    if api_key:
        try:
            import json as _json
            import httpx
            prompt = (
                "You are an Oracle Fusion FBDI data-quality expert. Propose VALIDATION and "
                "CLEANSING rules for the interface object '" + target_object + "'.\n"
                "Validation rule_type in " + str(sorted(_VALID_TYPES)) + "; cleansing rule_type in "
                + str(sorted(_CLEANSE_TYPES)) + ".\n"
                "For each field decide sensible rules (e.g. REQUIRED, MAX_LENGTH{max_length}, "
                "VALUE_IN_SET{values}, REGEX{pattern} for email/phone/date, NUMERIC, NOT_NEGATIVE; "
                "cleansing TRIM, UPPERCASE for codes, DEFAULT_IF_BLANK{value}). Only propose rules that "
                "clearly help; do not invent value sets. Use the samples to infer formats.\n"
                "Return STRICT JSON: {\"proposals\":[{\"kind\":\"validation|cleansing\",\"field\":\"..\","
                "\"rule_type\":\"..\",\"params\":{},\"severity\":\"error|warning\",\"description\":\"..\"}]}\n\n"
                "FIELDS:\n" + _json.dumps(cat)[:12000]
            )
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": model, "max_tokens": 4000,
                          "messages": [{"role": "user", "content": prompt}]},
                )
                dbg["http_status"] = r.status_code
                data = r.json()
                if isinstance(data, dict) and data.get("type") == "error":
                    dbg["api_error"] = str(data.get("error"))[:300]
                text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                if not text:
                    dbg["no_text"] = str(data)[:300]
                m = text[text.find("{"): text.rfind("}") + 1]
                doc = _json.loads(m) if m else {"proposals": []}
                _raw = doc.get("proposals", [])
                dbg["raw_proposals"] = len(_raw)
                dbg["dropped_types"] = sorted({str(p.get("rule_type", "")).upper() for p in _raw
                                               if str(p.get("rule_type", "")).upper() not in (_VALID_TYPES | _CLEANSE_TYPES)})[:10]
                for p in _raw:
                    rt = str(p.get("rule_type", "")).upper()
                    kind = str(p.get("kind", "validation")).lower()
                    if rt in (_VALID_TYPES | _CLEANSE_TYPES) and p.get("field"):
                        proposals.append({"kind": "cleansing" if rt in _CLEANSE_TYPES else "validation",
                                          "field": p.get("field"), "rule_type": rt,
                                          "params": p.get("params") or {},
                                          "severity": p.get("severity") or "error",
                                          "description": p.get("description") or ""})
                ai_used = bool(proposals)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_propose_rules AI call failed: %s", exc)
            dbg["exception"] = f"{type(exc).__name__}: {exc}"[:300]

    if not proposals:
        proposals = _deterministic_proposals(fields)
    return {"target_object": target_object, "ai_used": ai_used,
            "proposal_count": len(proposals), "proposals": proposals, "_debug": dbg}
