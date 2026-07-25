"""Agentic conversion — PLAN step (Epic A, slice 1).

Given a project, draft — but do NOT execute — the conversion plan for every
interface object: what the agent would map, generate and validate, in order, with
the precedence layer each decision comes from and any blockers that need a human
first. The plan is presented at a checkpoint so an analyst approves/edits before
anything runs. This slice is read-only: it proposes, it never mutates or loads.

``plan_steps_for`` is a PURE planner over a small signal dict (unit-testable);
``build_object_plan`` / ``build_project_plan`` are the async gatherers that assemble
the signals from each conversion's real state (reusing the copilot grounding facts +
readiness).
"""
from __future__ import annotations

from typing import Optional


def plan_steps_for(sig: dict) -> list[dict]:
    """Draft the ordered plan steps for one interface from its signals.

    ``sig``: has_source(bool), unmapped_required(int), has_mappings(bool),
    output_generated(bool), dq_generated(bool), dq_hard_errors(int).
    Each step: ``{action, detail, layer, blocker}``. ``layer`` names the precedence
    source the agent would use, so the plan is explainable."""
    steps: list[dict] = []
    if not sig.get("has_source"):
        steps.append({"action": "Bind a source file", "blocker": True, "layer": "input",
                      "detail": "No source is attached yet — the agent can't map this object until one is provided."})
        return steps

    unmapped = int(sig.get("unmapped_required", 0) or 0)
    has_maps = bool(sig.get("has_mappings"))
    if unmapped > 0:
        steps.append({"action": f"Auto-map {unmapped} unmapped required field(s)", "blocker": False,
                      "layer": "gold → learnings → workbook → deterministic → AI (residual)",
                      "detail": "Apply the mapping precedence; AI only fills what deterministic rules and learnings don't."})
    elif not has_maps:
        steps.append({"action": "Auto-map all fields", "blocker": False,
                      "layer": "gold → learnings → workbook → deterministic → AI (residual)",
                      "detail": "This object has no mappings yet — draft a full mapping set for review."})
    else:
        steps.append({"action": "Re-check mappings", "blocker": False, "layer": "learnings",
                      "detail": "Required fields are covered; confirm learned/gold rules are applied."})

    steps.append({"action": "Generate merged output", "blocker": False, "layer": "engine + merge/de-dup",
                  "detail": "Convert each source with its own mapping and converge into one file per interface."})
    steps.append({"action": "Run pre-load validation", "blocker": False, "layer": "data quality",
                  "detail": "Predict what Oracle would reject (cleanse + validate) without loading."})

    if sig.get("dq_generated") and int(sig.get("dq_hard_errors", 0) or 0) > 0:
        n = int(sig["dq_hard_errors"])
        steps.append({"action": f"Resolve {n} data-quality hard error(s)", "blocker": True, "layer": "data quality",
                      "detail": "These would block the Oracle load — fix before proceeding."})
    return steps


def _object_status(sig: dict, readiness: Optional[dict]) -> str:
    if not sig.get("has_source"):
        return "Blocked — no source"
    if sig.get("dq_generated") and int(sig.get("dq_hard_errors", 0) or 0) > 0:
        return "Needs fixes"
    if int(sig.get("unmapped_required", 0) or 0) > 0:
        return "Needs mapping"
    if readiness and readiness.get("band") == "Ready":
        return "Ready"
    return "In progress"


async def build_object_plan(conversion) -> dict:
    from app.services.copilot_grounding import build_conversion_facts
    facts = await build_conversion_facts(conversion)
    has_source = bool(getattr(conversion, "dataset_id", None)
                      or getattr(conversion, "dataset_ids", None)
                      or getattr(conversion, "source_type", "") == "ebs")
    has_mappings = any(m.get("source_column") or m.get("default_value") for m in facts.get("mapped", []))
    sig = {
        "has_source": has_source,
        "unmapped_required": len(facts.get("unmapped_required", [])),
        "has_mappings": has_mappings,
        "output_generated": facts.get("dq", {}).get("generated", False),
        "dq_generated": facts.get("dq", {}).get("generated", False),
        "dq_hard_errors": facts.get("dq", {}).get("hard_error_count", 0),
    }
    steps = plan_steps_for(sig)
    return {
        "conversion_id": str(conversion.id), "name": conversion.name,
        "target_object": facts.get("target_object"),
        "status": _object_status(sig, facts.get("readiness")),
        "readiness": facts.get("readiness"),
        "required_total": facts.get("required_total"),
        "required_covered": facts.get("required_covered"),
        "unmapped_required": facts.get("unmapped_required", [])[:15],
        "steps": steps,
        "has_blocker": any(s["blocker"] for s in steps),
    }


async def build_project_plan(project_id) -> dict:
    from beanie import PydanticObjectId
    from app.models.conversion import Conversion
    convs = await Conversion.find(
        Conversion.project_id == PydanticObjectId(str(project_id))
    ).sort(+Conversion.planned_load_order).to_list()
    objects = []
    for c in convs:
        try:
            objects.append(await build_object_plan(c))
        except Exception:  # noqa: BLE001 — one bad object shouldn't sink the plan
            continue
    total_steps = sum(len(o["steps"]) for o in objects)
    blocked = [o["name"] for o in objects if o["has_blocker"]]
    ready = [o for o in objects if o["status"] == "Ready"]
    return {
        "project_id": str(project_id),
        "object_count": len(objects),
        "total_steps": total_steps,
        "blocked_objects": blocked,
        "ready_count": len(ready),
        "requires_review": True,   # this is a checkpoint — nothing runs without approval
        "note": "Draft plan only — no mapping, generation or load has run. Review and approve to proceed.",
        "objects": objects,
    }
