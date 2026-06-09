"""LLM-backed mapping providers (Anthropic, OpenAI).

Used only when AI_PROVIDER is set and a valid API key is available. They feed
column-name and sample-value context to an LLM and parse a JSON response. If
the LLM call fails for any reason, we transparently fall back to the
deterministic rule-based mapper so the product never breaks the workflow.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from app.ai.base import MappingProvider, MappingSuggestion, SourceColumn, TargetField
from app.ai.rule_based import RuleBasedMapper


def _build_prompt(source_columns: list[SourceColumn], target_fields: list[TargetField]) -> str:
    src_payload = [
        {
            "name": s.name,
            "type": s.inferred_type,
            "samples": s.sample_values[:5],
            "null_pct": s.null_percent,
        }
        for s in source_columns
    ]
    tgt_payload = [
        {
            "id": t.id,
            "name": t.field_name,
            "description": (t.description or "")[:200],
            "type": t.data_type,
            "max_length": t.max_length,
            "required": t.required,
        }
        for t in target_fields
    ]
    return (
        "You are an Oracle Fusion Cloud data conversion expert. Map each TARGET FBDI "
        "field to the best SOURCE column from the legacy dataset. For each target field, "
        "return a JSON object with: target_field_id, source_column (or null), confidence "
        "(0..1), reason (short), suggested_transformation (object with rule_type & config "
        "or null), review_required (bool).\n\n"
        f"SOURCE COLUMNS:\n{json.dumps(src_payload, indent=2)}\n\n"
        f"TARGET FIELDS:\n{json.dumps(tgt_payload, indent=2)}\n\n"
        'Return ONLY a JSON array, no commentary. Example:\n'
        '[{"target_field_id":1,"source_column":"item_num","confidence":0.92,'
        '"reason":"name match + samples are identifiers",'
        '"suggested_transformation":{"rule_type":"REMOVE_HYPHEN","config":{}},'
        '"review_required":false}]'
    )


def _parse_response(text: str, target_fields: list[TargetField]) -> list[MappingSuggestion]:
    # Allow LLMs that wrap JSON in markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    arr = json.loads(cleaned)
    by_id = {t.id: t.field_name for t in target_fields}
    out: list[MappingSuggestion] = []
    for item in arr:
        tid = int(item["target_field_id"])
        out.append(
            MappingSuggestion(
                target_field_id=tid,
                target_field_name=by_id.get(tid, ""),
                source_column=item.get("source_column"),
                confidence=float(item.get("confidence", 0.0)),
                reason=item.get("reason", ""),
                suggested_transformation=item.get("suggested_transformation"),
                review_required=bool(item.get("review_required", True)),
            )
        )
    return out


class AnthropicMapper:
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self._fallback = RuleBasedMapper()

    def suggest_mappings(
        self, source_columns: list[SourceColumn], target_fields: list[TargetField]
    ) -> list[MappingSuggestion]:
        try:
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 4000,
                    "messages": [{"role": "user", "content": _build_prompt(source_columns, target_fields)}],
                },
                timeout=60.0,
            )
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return _parse_response(text, target_fields)
        except Exception:
            return self._fallback.suggest_mappings(source_columns, target_fields)


class OpenAIMapper:
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self._fallback = RuleBasedMapper()

    def suggest_mappings(
        self, source_columns: list[SourceColumn], target_fields: list[TargetField]
    ) -> list[MappingSuggestion]:
        try:
            r = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "You output strict JSON only."},
                        {"role": "user", "content": _build_prompt(source_columns, target_fields)
                            + "\nWrap the JSON array under the key \"mappings\"."},
                    ],
                },
                timeout=60.0,
            )
            r.raise_for_status()
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            obj = json.loads(content)
            arr = obj.get("mappings", obj if isinstance(obj, list) else [])
            return _parse_response(json.dumps(arr), target_fields)
        except Exception:
            return self._fallback.suggest_mappings(source_columns, target_fields)
