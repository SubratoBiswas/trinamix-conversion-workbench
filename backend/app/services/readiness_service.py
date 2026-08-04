"""Object-readiness / effort scoring for cutover planning.

Rolls the signals the tool already holds — required-field coverage, generate-time
data-quality status, whether a gold standard is on file, whether output has been
generated, and the last load result — into a single readiness score (0-100), a
band (Not started / In progress / Needs minor work / Ready / Blocked), and a rough
effort estimate (open items → Low/Medium/High + est. hours). Lets a cutover lead
see, at a glance, which interface objects are load-ready and which need work.

The scoring (``score_readiness``) is a pure function so it is unit-testable with no
DB; ``assess_conversion`` / ``assess_project`` are the Beanie gatherers that feed it.
"""
from __future__ import annotations

from typing import Optional

from app.services.mapping_dedupe import best_mapping_by_target  # noqa: E402


def score_readiness(sig: dict) -> dict:
    """Pure scorer. ``sig`` keys: required_total, required_covered, has_source,
    has_gold, output_generated, last_load_status ('passed'|'failed'|None),
    dq_hard_errors, dq_warnings, duplicate_clusters, anomaly_errors."""
    req_total = int(sig.get("required_total", 0) or 0)
    req_cov = int(sig.get("required_covered", 0) or 0)
    has_source = bool(sig.get("has_source", False))
    has_gold = bool(sig.get("has_gold", False))
    output = bool(sig.get("output_generated", False))
    last_load = sig.get("last_load_status")
    dq_hard = int(sig.get("dq_hard_errors", 0) or 0)
    dq_warn = int(sig.get("dq_warnings", 0) or 0)
    dupes = int(sig.get("duplicate_clusters", 0) or 0)
    anom_err = int(sig.get("anomaly_errors", 0) or 0)

    coverage = (req_cov / req_total) if req_total else (1.0 if has_source else 0.0)
    c_dq = 1.0 if dq_hard == 0 else max(0.0, 1 - 0.2 * dq_hard)
    c_ready = 0.5 * (1 if has_source else 0) + 0.5 * (1 if has_gold else 0)
    c_out = 0.4 * (1 if output else 0) + 0.6 * (1 if last_load == "passed" else 0)
    score = round(100 * (0.45 * coverage + 0.20 * c_dq + 0.15 * c_ready + 0.20 * c_out))
    score = max(0, min(100, score))

    blocked = dq_hard > 0 or last_load == "failed" or (req_total > 0 and coverage < 0.5 and has_source)
    if not has_source:
        band = "Not started"
    elif blocked:
        band = "Blocked"
    elif score >= 85:
        band = "Ready"
    elif score >= 60:
        band = "Needs minor work"
    else:
        band = "In progress"

    open_items = max(0, req_total - req_cov) + dq_hard + dupes + anom_err
    effort = ("None" if open_items == 0 else "Low" if open_items <= 5
              else "Medium" if open_items <= 15 else "High")
    est_hours = round(0.25 * max(0, req_total - req_cov) + 1.0 * dq_hard
                      + 0.3 * dupes + 0.2 * anom_err, 1)

    factors = [
        {"label": "Source data", "ok": has_source,
         "detail": "Source file bound" if has_source else "No source yet"},
        {"label": "Required fields",
         "ok": req_total > 0 and req_cov >= req_total,
         "detail": f"{req_cov}/{req_total} covered" if req_total else "n/a"},
        {"label": "Data quality", "ok": dq_hard == 0,
         "detail": ("clean" if dq_hard == 0 and output else
                    f"{dq_hard} hard error(s)" if dq_hard else "not generated yet")},
        {"label": "Gold standard", "ok": has_gold,
         "detail": "on file" if has_gold else "none"},
        {"label": "Output", "ok": output, "detail": "generated" if output else "not generated"},
        {"label": "Last load", "ok": last_load == "passed",
         "detail": last_load or "not loaded"},
    ]
    return {
        "score": score, "band": band, "effort": effort, "est_hours": est_hours,
        "open_items": open_items, "coverage_pct": round(coverage * 100),
        "factors": factors, "signals": sig,
    }


async def assess_conversion(conversion) -> dict:
    """Gather signals for one conversion and score it."""
    from app.models.fbdi import FBDIField, FBDITemplate, GoldStandard
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput
    from app.models.load import LoadRun

    target = conversion.target_object or ""
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    if template and not target:
        target = template.business_object or ""

    fields = await FBDIField.find(FBDIField.template_id == conversion.template_id).to_list() \
        if conversion.template_id else []
    required = [f for f in fields if f.required]
    maps = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    best = best_mapping_by_target(maps)
    covered = 0
    for f in required:
        m = best.get(f.id)
        mapped = bool(m and m.source_column and (m.status not in ("rejected", "not_applicable")))
        defaulted = bool((m and m.default_value) or f.default_if_blank)
        if mapped or defaulted:
            covered += 1

    has_source = bool(getattr(conversion, "dataset_id", None)
                      or getattr(conversion, "dataset_ids", None)
                      or getattr(conversion, "source_type", "") == "ebs")

    has_gold = bool(target) and bool(await GoldStandard.find(
        {"target_object": {"$regex": f"^{target}$", "$options": "i"}}).first_or_none())

    out = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == conversion.id).sort("-generated_at").first_or_none()
    dq = (out.dq_report if out else None) or {}
    output_generated = out is not None

    lr = await LoadRun.find(LoadRun.conversion_id == conversion.id).sort("-created_at").first_or_none()
    last_load = None
    if lr:
        if getattr(lr, "failed_count", 0):
            last_load = "failed"
        elif getattr(lr, "passed_count", 0) or getattr(lr, "status", "") in ("succeeded", "passed", "completed"):
            last_load = "passed"
        else:
            last_load = getattr(lr, "status", None)

    sig = {
        "required_total": len(required), "required_covered": covered,
        "has_source": has_source, "has_gold": has_gold,
        "output_generated": output_generated,
        "last_load_status": last_load,
        "dq_hard_errors": int(dq.get("hard_error_count", 0) or 0),
        "dq_warnings": int(dq.get("warning_count", 0) or 0),
        "duplicate_clusters": 0, "anomaly_errors": 0,
    }
    res = score_readiness(sig)
    res.update({"conversion_id": str(conversion.id), "name": conversion.name,
                "target_object": target})
    return res


async def assess_project(project_id) -> dict:
    """Score every conversion in a project + a project-level rollup."""
    from beanie import PydanticObjectId
    from app.models.conversion import Conversion
    convs = await Conversion.find(Conversion.project_id == PydanticObjectId(str(project_id))).to_list()
    objects = []
    for c in convs:
        try:
            objects.append(await assess_conversion(c))
        except Exception:  # noqa: BLE001 — one bad object shouldn't sink the report
            continue
    objects.sort(key=lambda o: o["score"])
    n = len(objects)
    avg = round(sum(o["score"] for o in objects) / n) if n else 0
    bands: dict = {}
    for o in objects:
        bands[o["band"]] = bands.get(o["band"], 0) + 1
    return {
        "project_id": str(project_id), "object_count": n, "avg_score": avg,
        "ready": bands.get("Ready", 0), "blocked": bands.get("Blocked", 0),
        "bands": bands, "total_est_hours": round(sum(o["est_hours"] for o in objects), 1),
        "objects": objects,
    }
