"""Output, load, workflow, dependency, and dashboard endpoints."""
# deploy-nudge: re-trigger backend build for wave-2 (commit 8782692)
import io
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.models.conversion import Conversion
from app.models.dependency import Dependency
from app.models.load import LoadError, LoadRun
from app.models.output import ConvertedOutput
from app.models.user import User
from app.models.workflow import Workflow
from app.schemas.misc import (
    DashboardKpis, DependencyOut, WorkflowCreate, WorkflowOut, WorkflowUpdate,
)
from app.schemas.runtime import (
    ConvertedOutputOut, LoadErrorOut, LoadRunOut, LoadSummaryOut, OutputPreviewOut,
)
from app.services.auth_service import get_current_user
from app.services.dashboard_service import get_kpis
from app.services.output_service import generate_output_artifact, get_output_preview
from app.services.quality_service import build_load_summary, simulate_conversion_load

log = logging.getLogger(__name__)


async def _require_conversion(conversion_id: str) -> Conversion:
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    return c


# ----- OUTPUT -----
output_router = APIRouter(prefix="/api/conversions", tags=["output"])


async def _run_generation(conversion_id: str, fmt: str,
                          include_header: bool | None = None) -> None:
    """Background worker: build the FBDI artifact off the request thread and record
    the outcome on the conversion, so the UI can poll instead of holding a long HTTP
    request (which the free-tier gateway kills at ~100s → surfaces as a CORS error)."""
    try:
        c = await Conversion.get(PydanticObjectId(conversion_id))
        if not c:
            return
        await generate_output_artifact(c, fmt=fmt, include_header=include_header)
        await c.set({"output_status": "ready", "output_error": None,
                     "updated_at": datetime.utcnow()})
    except Exception as exc:  # noqa: BLE001
        # THE TYPE AND THE PLACE, not just str(exc). Supplier Site failed with
        # output_error "0" — which is str(KeyError(0)) and says nothing about where.
        # A bare message is enough to prove something broke and never enough to fix
        # it, and this is a background worker, so there is no response body and no
        # request log to fall back on: whatever is recorded here IS the diagnosis.
        import traceback
        _tb = traceback.extract_tb(exc.__traceback__)
        _where = " <- ".join(
            f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in _tb[-4:])
        _msg = f"{type(exc).__name__}: {exc} | at {_where}"
        log.exception("generation failed for %s", conversion_id)
        try:
            c = await Conversion.get(PydanticObjectId(conversion_id))
            if c:
                await c.set({"output_status": "failed", "output_error": _msg[:900]})
        except Exception:
            pass


@output_router.post("/{conversion_id}/generate-output")
async def generate_output(
    conversion_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx|template)$"),
    wait: bool = Query(False, description="Block until done (legacy). Default is async."),
    include_header: bool | None = Query(
        None, description="Include the column-label header row. None=auto "
        "(supplier FBDI headerless, others with header); true/false forces it."),
    _: User = Depends(get_current_user),
):
    """Kick off FBDI generation. By default this returns immediately with
    status='generating' and the work runs in the background — the client polls
    /generation-status and downloads when ready. Heavy multi-sheet objects
    (Customer/Item) otherwise blow the gateway timeout. Pass wait=true for the old
    synchronous behaviour (used internally by the bundle build)."""
    import asyncio

    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")

    if wait:
        out = await generate_output_artifact(c, fmt=fmt, include_header=include_header)
        return {**out.model_dump(), "id": str(out.id),
                "conversion_id": str(out.conversion_id), "status": "ready"}

    await c.set({"output_status": "generating", "output_error": None,
                 "output_started_at": datetime.utcnow()})
    asyncio.create_task(_run_generation(conversion_id, fmt, include_header))
    return {"status": "generating", "conversion_id": conversion_id}


@output_router.get("/{conversion_id}/generation-status")
async def generation_status(conversion_id: str, _: User = Depends(get_current_user)):
    """Poll target for background generation. Returns generating/ready/failed and,
    when ready, the newest artifact so the client can download it."""
    c = await _require_conversion(conversion_id)
    status = c.output_status or "idle"

    # If a background task was lost (worker restart / OOM kill) the status can be
    # stuck on "generating" forever — treat a stale one as failed so the UI recovers.
    if status == "generating" and c.output_started_at:
        age = (datetime.utcnow() - c.output_started_at).total_seconds()
        if age > 600:
            status = "failed"
            await c.set({"output_status": "failed",
                         "output_error": "Generation timed out or the worker restarted. Try again."})

    out = None
    if status == "ready":
        latest = await ConvertedOutput.find(
            ConvertedOutput.conversion_id == c.id
        ).sort("-generated_at").first_or_none()
        if latest:
            out = {"id": str(latest.id), "file_name": latest.output_file_name,
                   "row_count": latest.row_count, "column_count": latest.column_count,
                   "dq_report": getattr(latest, "dq_report", None)}
    return {"status": status, "error": c.output_error, "output": out}


@output_router.get("/{conversion_id}/output-preview", response_model=OutputPreviewOut)
async def output_preview(
    conversion_id: str,
    limit: int = 50,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")
    return await get_output_preview(c, limit=limit)


@output_router.get("/{conversion_id}/output-preview-by-source")
async def output_preview_by_source(
    conversion_id: str,
    limit: int = 50,
    _: User = Depends(get_current_user),
):
    """Per-source converted preview (multi-source): one block per source file,
    each converted individually. The merged, de-duplicated output is produced at
    Generate. Single-source/EBS conversions return one block."""
    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")
    from app.services.output_service import get_output_preview_by_source
    return await get_output_preview_by_source(c, limit=limit)


@output_router.get("/{conversion_id}/reconciliation")
async def reconciliation(conversion_id: str, _: User = Depends(get_current_user)):
    """Reconcile the conversion: source record counts (per source file), the merged
    output record count, how many were merged/de-duplicated, and — if a load has
    run — the loaded/passed/failed counts, with a short narrative."""
    from app.models.dataset import Dataset
    c = await _require_conversion(conversion_id)
    sources = []
    src_total = 0
    for did in c.source_dataset_ids:
        ds = await Dataset.get(did)
        if ds:
            rc = int(ds.row_count or 0)
            src_total += rc
            sources.append({"name": ds.name, "rows": rc})
    latest_out = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == c.id).sort("-generated_at").first_or_none()
    out_rows = int(latest_out.row_count) if latest_out else None
    merged_removed = (src_total - out_rows) if (out_rows is not None and src_total) else None
    run = await LoadRun.find(LoadRun.conversion_id == c.id).sort("-completed_at").first_or_none()
    load = None
    if run:
        load = {"status": run.status, "total": run.total_records, "passed": run.passed_count,
                "failed": run.failed_count, "warnings": run.warning_count}
    parts = []
    if sources:
        parts.append(f"{len(sources)} source file(s) totalling {src_total} record(s)")
    if out_rows is not None:
        parts.append(f"merged output has {out_rows} record(s)")
    if merged_removed and merged_removed > 0:
        parts.append(f"{merged_removed} merged/de-duplicated away")
    if load:
        parts.append(f"last load: {load['passed']} passed / {load['failed']} failed of {load['total']}")
    return {"sources": sources, "source_total": src_total, "output_rows": out_rows,
            "merged_or_deduped": merged_removed, "load": load,
            "narrative": "; ".join(parts) + "." if parts else "No source/output yet."}


@output_router.get("/{conversion_id}/preload-report")
async def preload_report_endpoint(
    conversion_id: str,
    sample_rows: int = 3000,
    _: User = Depends(get_current_user),
):
    """Predictive pre-load DQ report: validate the merged converted frame and return
    a plain-English 'what Oracle will reject and how to fix' summary (no file written)."""
    c = await _require_conversion(conversion_id)
    is_ebs = getattr(c, "source_type", "dataset") == "ebs"
    if not c.template_id or (not is_ebs and not c.dataset_id):
        raise HTTPException(400, "Conversion is not fully bound")
    from app.services.output_service import preload_report
    return await preload_report(c, sample_rows=sample_rows)


@output_router.get("/{conversion_id}/merged-preview")
async def merged_preview(conversion_id: str, limit: int = 50, _: User = Depends(get_current_user)):
    """Preview the MERGED output for this conversion's interface — every source
    conversion for the same target object in the project, converted with its own
    mapping and merged/de-duplicated into one result (what the merged file will
    contain). If there's only one source, it's just that conversion's preview."""
    c = await _require_conversion(conversion_id)
    from app.services.output_service import (build_merged_frame_for_object,
                                             _mask_supplier_emails)
    merged, carrier, names = await build_merged_frame_for_object(
        c.project_id, c.target_object or "", max_rows=max(limit * 4, 200))
    if merged is None:
        from app.services.output_service import get_output_preview
        p = await get_output_preview(c, limit=limit)
        p["sources"] = names
        return p
    head = merged.head(limit)
    if "supplier" in (c.target_object or "").lower():
        try:
            head = _mask_supplier_emails(head.copy())
        except Exception:  # noqa: BLE001
            pass
    return {"columns": list(head.columns.astype(str)),
            "rows": head.fillna("").to_dict(orient="records"),
            "total_rows": int(len(merged)), "sources": names,
            "target_object": c.target_object}


@output_router.get("/{conversion_id}/duplicate-candidates")
async def duplicate_candidates(
    conversion_id: str,
    threshold: float = Query(0.86, ge=0.5, le=1.0),
    use_ai: bool = Query(False),
    max_rows: int = Query(8000, ge=100, le=50000),
    # The scanner caps the clusters it RETURNS while `cluster_count` reports the
    # true total. At the old fixed cap of 100 a 343-cluster scan left 243 groups
    # unreachable — and the decided/undecided counters described only the visible
    # slice, so the screen read as fully reviewed when it was not.
    max_clusters: int = Query(100, ge=10, le=2000),
    _: User = Depends(get_current_user),
):
    """Fuzzy duplicate / entity resolution over this interface's MERGED data —
    surface records that are likely the SAME entity despite non-identical keys or
    names (what the exact-key de-dup can't catch). Deterministic clustering; set
    ``use_ai=true`` to have the model adjudicate borderline clusters."""
    c = await _require_conversion(conversion_id)
    from app.services.output_service import (build_merged_frame_for_object,
                                             build_converted_dataframe)
    from app.services.entity_resolution import (find_duplicate_clusters,
                                                 ai_adjudicate_clusters)
    merged, carrier, names = await build_merged_frame_for_object(
        c.project_id, c.target_object or "", max_rows=max_rows)
    if merged is None:
        merged = (await build_converted_dataframe(c))[0]
    import asyncio as _aio
    result = await _aio.to_thread(
        find_duplicate_clusters, merged, c.target_object or "", threshold=threshold,
        max_rows=max_rows, max_clusters=max_clusters)
    result["sources"] = names
    if use_ai:
        result = await ai_adjudicate_clusters(result)
    # Attach the STABLE identity keys plus any verdict already recorded — for this
    # conversion, or learned from an earlier one for the same client + object. The
    # scanner reports positional row indices, which are meaningless once the frame
    # is rebuilt at generation time; the UI must save decisions against these keys.
    from app.services.decision_service import (annotate_clusters, load_decisions,
                                               load_learned_keep_all,
                                               identity_columns_for)
    decided = {d["decision_key"]: d for d in await load_decisions(c.id)}
    learned = await load_learned_keep_all(
        getattr(c, "client_id", None) or await _conversion_client_id(c), c.target_object)
    # find_duplicate_clusters reports identity_fields as plain column names in the
    # happy path but as {column, kind, weight} dicts on the early-return branches —
    # accept either rather than assume.
    idc = [f if isinstance(f, str) else f.get("column")
           for f in (result.get("identity_fields") or [])]
    idc = [c2 for c2 in idc if c2] or identity_columns_for(merged, c.target_object)
    result["clusters"] = annotate_clusters(result.get("clusters", []), merged, idc,
                                           decided=decided, learned_keep_all=learned)
    result["identity_columns"] = idc
    result["decided_count"] = sum(1 for cl in result["clusters"] if cl.get("decision"))
    # Undecided counts only what was RETURNED. `hidden_count` names the rest
    # explicitly rather than letting the difference against `cluster_count` pass
    # unnoticed — a silently truncated review list reads as a completed one.
    result["undecided_count"] = len(result["clusters"]) - result["decided_count"]
    result["returned_count"] = len(result["clusters"])
    result["hidden_count"] = max(
        0, int(result.get("cluster_count") or 0) - len(result["clusters"]))
    result["max_clusters"] = max_clusters
    return result


async def _conversion_client_id(c):
    """The client a conversion belongs to (via its project) — decisions are
    promoted to client scope, so this is what makes a verdict reusable."""
    try:
        from app.models.project import Project
        p = await Project.get(c.project_id)
        return getattr(p, "client_id", None)
    except Exception:  # noqa: BLE001
        return None


@output_router.get("/{conversion_id}/review")
async def review_bundle(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    """Everything the user has to adjudicate before the file is generated, in one
    payload: open cleansing/validation findings plus the counts the generation gate
    warns on. Duplicates come from ``/duplicate-candidates`` (it is slow and
    parameterised, so it stays a separate lazy call)."""
    c = await _require_conversion(conversion_id)
    from app.models.validation import ValidationIssue
    from app.models.row_decision import RowDecision
    issues = await ValidationIssue.find(
        ValidationIssue.conversion_id == c.id).limit(500).to_list()
    dec = await RowDecision.find(RowDecision.conversion_id == c.id).to_list()
    cleansed = {d.decision_key: d.verdict for d in dec if d.scope == "cleansing"}
    out = []
    for i in issues:
        key = f"{i.field_name or ''}|{i.issue_type or ''}"
        out.append({"key": key, "category": i.category, "field_name": i.field_name,
                    "issue_type": i.issue_type, "severity": i.severity,
                    "message": i.message, "suggested_fix": i.suggested_fix,
                    "auto_fixable": i.auto_fixable, "impacted_count": i.impacted_count,
                    "verdict": cleansed.get(key)})
    return {"conversion_id": str(c.id), "target_object": c.target_object,
            "cleansing": out,
            "cleansing_open": sum(1 for o in out if not o["verdict"]),
            "duplicate_decisions": sum(1 for d in dec if d.scope == "duplicate")}


async def run_required_check(conversion_id: str, max_rows: int = 20000) -> dict:
    """The required-field gate, callable from ordinary Python.

    Kept SEPARATE from the endpoint because the endpoint's ``max_rows`` default is a
    FastAPI ``Query(...)`` object. Calling the endpoint function directly (as
    ``mapping-report`` did) therefore handed a ``Query`` instance to pandas'
    ``nrows``, the call raised, the caller's ``except`` swallowed it, and the report's
    whole required-field section silently read zero — on every conversion, since the
    deployment. Confirmed live on 29-Jul: ``include_required=true`` and ``=false``
    returned byte-identical sections.
    """
    from app.services import required_fields_service as rf
    from app.services.output_service import build_sheet_frames
    import asyncio as _aio

    c = await _require_conversion(conversion_id)
    obj = c.target_object or ""
    required = rf.load_required(obj)
    if not required:
        return {"conversion_id": str(c.id), "target_object": obj,
                "required_total": 0, "failed_count": 0, "partial_count": 0,
                "blocked": False, "sheets": [], "failures": [], "partials": [],
                "message": f"No curated required-field list for {obj or 'this object'}."}

    # Per-interface-sheet frames, routed and defaulted exactly as generation writes
    # them. This used to pass ``collect_frames`` straight through — but that dict is
    # keyed by DATASET ID, so no required sheet name ever matched, every field read
    # as absent, and the gate returned blocked=true on healthy conversions. A gate
    # that always fires is worse than no gate. Found by the 29-Jul live run against
    # the deployed service, not by the unit tests: those fed check_sheets
    # sheet-named frames directly and so never crossed this seam.
    sheets, df = await build_sheet_frames(c, max_rows=max_rows)
    # Objects with no sheeted template: the merged frame IS what generation writes.
    if not sheets:
        sheets = {name: df for name in required}
    # Which interface sheets does THIS conversion actually own? The first fix keyed
    # the frames correctly and the gate still fired, because the vocabularies differ:
    # the template calls its sheet POZ_SUPPLIERS_INT and the curated list calls it
    # "Supplier Import". And the Supplier bundle spans six interface tables while one
    # template declares one of them — so five curated sheets belong to sibling
    # conversions and must not block this one. Aliases resolve the naming; owned_sheets
    # resolves the scope.
    res = await _aio.to_thread(rf.check_sheets, sheets, required,
                               owned_sheets=list(sheets.keys()),
                               aliases=rf.load_sheet_aliases(obj))
    res.update({"conversion_id": str(c.id), "target_object": obj,
                "message": rf.explain(res)})
    return res


@output_router.get("/{conversion_id}/required-check")
async def required_check(
    conversion_id: str,
    max_rows: int = Query(20000, ge=100, le=200000),
    _: User = Depends(get_current_user),
):
    """Do the object's required fields actually hold values in the built output?

    Checked on the FINISHED frames rather than on the mappings, because a field
    can be mapped to a column that exists but is empty, mapped to a column absent
    from this extract, or satisfied by a control default with no mapping at all.
    Only the output gets all three right (§10.1).
    """
    return await run_required_check(conversion_id, max_rows=max_rows)


@output_router.post("/{conversion_id}/apply-customer-rules")
async def apply_customer_rules(
    conversion_id: str,
    replace: bool = Query(False, description="Re-apply the authored config over an "
                                             "existing rule for the same field"),
    _: User = Depends(get_current_user),
):
    """Author CW_Issues rows 15-24 onto this Customer conversion.

    These were the "Authorable" rows: the engine could express each one and nobody had
    typed it, because the source column spellings come from the analyst's extract.
    Authored as data on the 30-Jul instruction — same destination and precedence as
    typing them into the rule box, just pre-filled, with every source column expressed
    as a list of candidate spellings so a prose spelling that does not match the extract
    costs nothing instead of binding to nothing.

    ``replace=False`` never clobbers a hand-edited rule. The response carries the open
    questions (the two differing Party Number key widths, and the section 10.6 conflict
    the analyst's instruction overrides) rather than leaving them in a code comment.
    """
    from app.services import customer_rules_service as crs
    c = await _require_conversion(conversion_id)
    res = await crs.apply_to_conversion(c, replace=replace)
    if res.get("applied") or res.get("updated"):
        # The output no longer matches the rules that would now run.
        from app.models.output import ConvertedOutput as _CO
        await _CO.find(_CO.conversion_id == c.id).update({"$set": {"status": "stale"}})
    return {"conversion_id": str(c.id), "target_object": c.target_object, **res}


@output_router.post("/{conversion_id}/column-rules/fix")
async def fix_column_rule(
    conversion_id: str,
    payload: dict,
    _: User = Depends(get_current_user),
):
    """Build the rule that fixes one column-rule finding, and learn it.

    The Cleansing tab names exactly what Oracle will reject and what to do about it.
    Every one of those has a single obvious remedy, so making the analyst retype it in
    another screen is asking them to re-derive something the tool already knows.

    Body: the finding, as returned by GET column-rules. Only the ones whose remedy
    involves no judgement are built — a value outside the accepted codes, a number too
    big for its column and a missing mandatory value are all refused WITH the reason,
    because a button that quietly truncates a mis-mapped number or picks a code on the
    analyst's behalf turns a visible problem into an invisible one.

    The rule is also captured as a learning, per the standing instruction that a
    correction made once should reach every current and future conversion.
    """
    from app.models.fbdi import FBDIField, FBDITemplate
    from app.models.output import ConvertedOutput
    from app.models.transformation import TransformationRule
    from app.services.column_rule_fix_service import plan_fix

    c = await _require_conversion(conversion_id)
    plan = plan_fix(payload or {})
    if not plan.get("ok"):
        raise HTTPException(422, plan.get("reason") or "No automatic fix for this.")

    field_name = str((payload or {}).get("field") or "")
    template = await FBDITemplate.get(c.template_id) if c.template_id else None
    if template is None:
        raise HTTPException(400, "This conversion has no template bound.")
    import re as _re
    def _n(x):
        return _re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    fields = [f for f in await FBDIField.find(
        FBDIField.template_id == template.id).to_list()
        if _n(f.field_name) == _n(field_name)]
    if not fields:
        raise HTTPException(404, f"No field named {field_name!r} in this template.")

    made = 0
    for f in fields:
        existing = await TransformationRule.find_one({
            "conversion_id": c.id, "target_field_id": f.id,
            "rule_type": plan["rule_type"]})
        if existing:
            await existing.set({"rule_config": plan["rule_config"],
                                "description": plan["description"]})
        else:
            await TransformationRule(
                conversion_id=c.id, target_field_id=f.id,
                rule_type=plan["rule_type"], rule_config=plan["rule_config"],
                description=plan["description"], sequence=50,
            ).insert()
        made += 1

    # The file on disk no longer matches the rules that would now run.
    await ConvertedOutput.find(
        ConvertedOutput.conversion_id == c.id).update({"$set": {"status": "stale"}})

    learned = None
    try:
        from app.services.learning_service import record_learning_from_rule
        rule = await TransformationRule.find_one({
            "conversion_id": c.id, "target_field_id": fields[0].id,
            "rule_type": plan["rule_type"]})
        if rule is not None:
            learned = await record_learning_from_rule(
                rule, c, captured_by=getattr(_, "email", ""))
    except Exception:                                           # noqa: BLE001
        log.exception("column-rule fix: learning capture failed for %s", field_name)

    return {"conversion_id": str(c.id), "field": field_name,
            "rule_type": plan["rule_type"], "rule_config": plan["rule_config"],
            "description": plan["description"], "bindings": made,
            "learned": bool(learned), "output_marked_stale": True}


@output_router.get("/{conversion_id}/column-rules")
async def column_rules(
    conversion_id: str,
    max_rows: int = Query(20000, ge=100, le=200000),
    _: User = Depends(get_current_user),
):
    """The built output checked against the rules Oracle states in its own template.

    Oracle puts a comment on every header cell giving the database truth for that
    column — "BATCH_ID / NOT NULL / NUMBER (18)". The parser never read them, so the
    validation engine had a length check, a numeric check, a date check and a
    value-set check and almost no metadata to run them on. These are transcriptions,
    not inferences, which is why they are shown as definite findings.

    Aggregated per COLUMN, not per row: 8,000 rows of "row N: too long" is a wall,
    one row saying "412 values exceed 240 characters, longest 380, examples …" is a
    finding somebody can act on.
    """
    from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
    from app.services import column_rules_service as cr
    from app.services.output_service import build_sheet_frames
    import asyncio as _aio

    c = await _require_conversion(conversion_id)
    template = await FBDITemplate.get(c.template_id) if c.template_id else None
    if template is None:
        return {"conversion_id": str(c.id), "target_object": c.target_object,
                "findings": [], "sheets": [], "columns_checked": 0,
                "columns_with_rules": 0, "error_count": 0, "warning_count": 0,
                "blocked": False,
                "message": "This conversion has no FBDI template bound, so there are "
                           "no published column rules to check against."}
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
    sheets = await FBDISheet.find(FBDISheet.template_id == template.id).to_list()
    sheet_name = {s.id: s.sheet_name for s in sheets}
    by_sheet: dict[str, list[dict]] = {}
    for f in fields:
        by_sheet.setdefault(sheet_name.get(f.sheet_id) or "", []).append({
            "field_name": f.field_name, "db_column": getattr(f, "db_column", None),
            "required": bool(f.required), "data_type": f.data_type,
            "max_length": f.max_length, "format_mask": f.format_mask,
            "precision": getattr(f, "precision", None),
            "scale": getattr(f, "scale", None),
            "do_not_populate": bool(getattr(f, "do_not_populate", False)),
            "allowed_values": getattr(f, "allowed_values", None) or [],
        })

    frames, merged = await build_sheet_frames(c, max_rows=max_rows)
    if not frames:
        frames = {name: merged for name in by_sheet} or {"": merged}

    per_sheet: dict[str, dict] = {}
    for sheet, df in frames.items():
        flds = by_sheet.get(sheet)
        if flds is None:
            # Sheet-name vocabularies differ (interface table vs workbook tab), so
            # fall back to every field rather than silently checking nothing.
            flds = [x for v in by_sheet.values() for x in v]
        per_sheet[sheet] = await _aio.to_thread(cr.check_frame, df, flds)
    out = cr.summarize(per_sheet)
    out.update({"conversion_id": str(c.id), "target_object": c.target_object,
                "template": template.name})
    return out


@output_router.get("/{conversion_id}/mapping-report")
async def mapping_report(
    conversion_id: str,
    include_required: bool = Query(True),
    _: User = Depends(get_current_user),
):
    """The post-mapping summary: coverage by layer, validation and cleansing
    pass/fail, and any required field with no value.

    These numbers already existed but were scattered across the canvas, the DQ
    report and the review tabs, so nobody read them together and a blocking gap
    was easy to miss.
    """
    from app.models.fbdi import FBDIField, FBDITemplate
    from app.models.mapping import MappingSuggestion
    from app.models.output import ConvertedOutput
    from app.models.transformation import TransformationRule
    from app.services import mapping_report_service as mr

    c = await _require_conversion(conversion_id)
    template = await FBDITemplate.get(c.template_id) if c.template_id else None
    fields = (await FBDIField.find(FBDIField.template_id == template.id).to_list()
              if template else [])
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == c.id).to_list()
    rules = await TransformationRule.find(
        TransformationRule.conversion_id == c.id).to_list()
    latest = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == c.id).sort("-generated_at").first_or_none()

    eff = {}
    try:
        from app.services.defaults_service import compute_effective_defaults
        eff = await compute_effective_defaults(c, use_ai=False) or {}
    except Exception:                                           # noqa: BLE001
        pass

    req = None
    req_error = None
    if include_required:
        try:
            # Plain helper, NOT the endpoint function — see run_required_check.
            req = await run_required_check(conversion_id)
        except Exception as _rq:                                # noqa: BLE001
            # Surface the reason instead of reporting a silent clean pass. The
            # previous bare `req = None` is why a total failure of this section
            # looked identical to "nothing required" for weeks.
            req = None
            req_error = f"{type(_rq).__name__}: {_rq}"[:300]
            log.exception("required-field gate failed for conversion %s",
                          conversion_id)

    rep = mr.build_report(
        conversion={"id": str(c.id), "target_object": c.target_object,
                    "generated_at": (latest.generated_at.isoformat()
                                     if latest and latest.generated_at else None)},
        fields=[{"id": f.id, "field_name": f.field_name,
                 "required": bool(f.required)} for f in fields],
        mappings=[{"target_field_id": m.target_field_id,
                   "source_column": m.source_column, "reason": m.reason,
                   "status": m.status, "confidence": m.confidence,
                   "default_value": getattr(m, "default_value", None)}
                  for m in maps],
        dq_report=(latest.dq_report if latest else None),
        required_result=req,
        effective_defaults=eff,
        rule_target_ids=[r.target_field_id for r in rules
                         if getattr(r, "target_field_id", None) is not None],
        custom_rules=[{"id": str(r.id)} for r in rules],
    )
    rep["output_stale"] = bool(latest and latest.status == "stale")
    if req_error:
        rep["required_fields"]["error"] = req_error
    return rep


@output_router.get("/{conversion_id}/cleansing-profile")
async def get_cleansing_profile(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    """Which cleansing families run at generation for this conversion.

    Returns the catalogue too, so the UI does not hardcode family names or which
    of them rewrite business values — that judgement belongs next to the rules.
    """
    from app.services import cleansing_rules as cr
    c = await _require_conversion(conversion_id)
    prof = getattr(c, "cleansing_profile", None) or cr.default_profile([])
    return {
        "conversion_id": str(c.id),
        "profile": prof,
        "is_default": not getattr(c, "cleansing_profile", None),
        "families": [
            {"key": f, "label": cr.FAMILY_LABEL[f],
             "safe": f in cr.SAFE_FAMILIES,
             "enabled": f in (prof.get("families") or [])}
            for f in cr.FAMILIES
        ],
    }


@output_router.put("/{conversion_id}/cleansing-profile")
async def put_cleansing_profile(
    conversion_id: str,
    payload: dict,
    _: User = Depends(get_current_user),
):
    """Replace the conversion's cleansing profile. Unknown family names are
    dropped rather than stored, so a typo cannot silently disable cleansing."""
    from app.services import cleansing_rules as cr
    c = await _require_conversion(conversion_id)
    fams = [f for f in (payload.get("families") or []) if f in cr.FAMILIES]
    per_field = {str(k): [f for f in (v or []) if f in cr.FAMILIES]
                 for k, v in (payload.get("per_field") or {}).items()}
    # Analyst corrections: {field: {original value: replacement}}. Stored verbatim
    # — an override is the reviewer overruling a rule, so it is not sanitised
    # against the family list the way per_field is.
    overrides = {str(k): {str(a): str(b) for a, b in (v or {}).items()}
                 for k, v in (payload.get("value_overrides") or {}).items()}
    profile = {
        "families": fams,
        "ascii_fold": bool(payload.get("ascii_fold")),
        "per_field": per_field,
        "exclude_fields": [str(x) for x in (payload.get("exclude_fields") or [])],
        "value_overrides": {k: v for k, v in overrides.items() if v},
    }
    c.cleansing_profile = profile
    c.updated_at = datetime.utcnow()
    await c.save()
    return {"conversion_id": str(c.id), "profile": profile}


@output_router.get("/{conversion_id}/cleansing-preview")
async def cleansing_preview(
    conversion_id: str,
    families: Optional[str] = Query(
        None, description="Comma-separated family keys; omit to use the saved profile."),
    max_rows: int = Query(2000, ge=50, le=20000),
    _: User = Depends(get_current_user),
):
    """Dry run: what the cleansing families WOULD change, without changing it.

    Deliberately previewable for families the conversion has not enabled — deciding
    whether to turn one on is the whole reason to look. Runs on the same converted
    frame generation uses, so the before/after pairs are the real values.
    """
    from app.services import cleansing_rules as cr
    from app.services.output_service import build_converted_dataframe
    import asyncio as _aio
    c = await _require_conversion(conversion_id)
    df, _lineage = await build_converted_dataframe(c, max_rows=max_rows)
    fam = ([f.strip() for f in families.split(",") if f.strip() in cr.FAMILIES]
           if families else None)
    from app.services.generate_dq import protected_values
    rep = await _aio.to_thread(
        cr.preview_frame, df, getattr(c, "cleansing_profile", None),
        families=fam, protected=protected_values())
    rep["rows_scanned"] = int(len(df)) if df is not None else 0
    rep["conversion_id"] = str(c.id)
    return rep


@output_router.post("/{conversion_id}/decisions")
async def save_decisions(
    conversion_id: str,
    payload: dict,
    user: User = Depends(get_current_user),
):
    """Record duplicate/cleansing verdicts. Upsert by ``decision_key`` so
    re-deciding a cluster replaces the earlier call instead of stacking.

    ``promote=true`` (default for keep_all) also makes the verdict reusable across
    conversions for this client + object, so the next extract does not re-ask about
    the same look-alike records."""
    from app.models.row_decision import RowDecision, DUP_VERDICTS, CLEANSE_VERDICTS
    c = await _require_conversion(conversion_id)
    client_id = getattr(c, "client_id", None) or await _conversion_client_id(c)
    saved = 0
    for d in (payload.get("decisions") or []):
        scope = (d.get("scope") or "duplicate").strip()
        verdict = (d.get("verdict") or "").strip()
        key = (d.get("decision_key") or "").strip()
        allowed = DUP_VERDICTS if scope == "duplicate" else CLEANSE_VERDICTS
        if not key or verdict not in allowed:
            continue
        existing = await RowDecision.find_one(
            RowDecision.conversion_id == c.id, RowDecision.decision_key == key)
        fields = dict(verdict=verdict, scope=scope, client_id=client_id,
                      target_object=c.target_object,
                      survivor_key=d.get("survivor_key"),
                      member_keys=d.get("member_keys") or [],
                      keep_keys=d.get("keep_keys") or [],
                      label=d.get("label"), note=d.get("note"),
                      decided_by=getattr(user, "email", None),
                      decided_at=datetime.utcnow(),
                      promoted=bool(d.get("promote", verdict == "keep_all")))
        if existing:
            await existing.set(fields)
        else:
            await RowDecision(conversion_id=c.id, decision_key=key, **fields).insert()
        saved += 1
    return {"saved": saved}


@output_router.delete("/{conversion_id}/decisions")
async def clear_decisions(
    conversion_id: str,
    decision_key: str | None = Query(None),
    _: User = Depends(get_current_user),
):
    """Undo one decision, or all of them for this conversion."""
    from app.models.row_decision import RowDecision
    c = await _require_conversion(conversion_id)
    q = [RowDecision.conversion_id == c.id]
    if decision_key:
        q.append(RowDecision.decision_key == decision_key)
    docs = await RowDecision.find(*q).to_list()
    for d in docs:
        await d.delete()
    return {"cleared": len(docs)}


@output_router.get("/{conversion_id}/cross-client-suggestions")
async def cross_client_suggestions(
    conversion_id: str,
    limit: int = Query(200, ge=1, le=500),
    _: User = Depends(get_current_user),
):
    """Cross-client mapping/crosswalk suggestions for this conversion's interface —
    decisions OTHER clients have approved for the SAME Fusion object, ranked by how
    many clients support each (advisory; the current client's own rows are excluded
    and nothing is auto-applied)."""
    c = await _require_conversion(conversion_id)
    from app.services.cross_client_service import suggest_for_object
    try:
        from app.services.client_service import client_id_for_conversion
        cid = await client_id_for_conversion(c)
    except Exception:  # noqa: BLE001
        cid = None
    target = c.target_object or ""
    if not target and c.template_id:
        from app.models.fbdi import FBDITemplate
        tpl = await FBDITemplate.get(c.template_id)
        target = (tpl.business_object if tpl else "") or ""
    res = await suggest_for_object(target, cid, limit=limit)
    res["client_id"] = str(cid) if cid else None
    return res


@output_router.get("/{conversion_id}/readiness")
async def conversion_readiness(conversion_id: str, _: User = Depends(get_current_user)):
    """Cutover-readiness score (0-100) + band + effort estimate for one conversion,
    rolled up from required-field coverage, DQ status, gold, output and load history."""
    c = await _require_conversion(conversion_id)
    from app.services.readiness_service import assess_conversion
    return await assess_conversion(c)


@output_router.get("/project/{project_id}/readiness")
async def project_readiness(project_id: str, _: User = Depends(get_current_user)):
    """Cutover-readiness across every interface object in a project + a rollup
    (avg score, ready/blocked counts, total estimated effort)."""
    from app.services.readiness_service import assess_project
    return await assess_project(project_id)


@output_router.post("/{conversion_id}/mapping-export")
async def mapping_export(
    conversion_id: str,
    body: dict,
    _: User = Depends(get_current_user),
):
    """Banded field-mapping export (Issue #3). The Mapping Review screen posts the
    rows it already computed (``records``: target_field / suggested_source /
    confidence / reason / excluded); returns the clean Excel workbook (Summary +
    one sheet per non-empty confidence band)."""
    import asyncio
    from app.services.mapping_export_service import build_workbook
    records = (body or {}).get("records") or []
    title = str((body or {}).get("title") or "Field Mapping")
    filename = str((body or {}).get("filename") or "Field_Mapping.xlsx")
    if not filename.lower().endswith(".xlsx"):
        filename += ".xlsx"
    data = await asyncio.to_thread(build_workbook, title, records)
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_safe_name(filename)}"'},
    )


@output_router.get("/project/{project_id}/agentic-plan")
async def agentic_plan(project_id: str, _: User = Depends(get_current_user)):
    """Agentic PLAN step (checkpoint): draft — but do NOT run — the per-interface
    conversion plan (map → generate → validate, with provenance and blockers) for
    every object in the project, for human review/approval. Read-only."""
    from app.services.agentic_planner import build_project_plan
    return await build_project_plan(project_id)


@output_router.post("/{conversion_id}/copilot")
async def conversion_copilot(
    conversion_id: str,
    body: dict,
    _: User = Depends(get_current_user),
):
    """Grounded, read-only copilot for ONE conversion. Answers a question strictly
    from this conversion's own mappings (with provenance), DQ report and readiness —
    deterministic, with an LLM layer when configured. Never mutates state."""
    c = await _require_conversion(conversion_id)
    question = str((body or {}).get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Provide a 'question'.")
    from app.services.copilot_grounding import answer_grounded
    return await answer_grounded(c, question)


async def _carrier_for_object(project_id, target_object: str):
    """The carrier conversion (first bound, by load order) for a project+interface —
    the merged artifact is stored under it and its status is polled."""
    convs = await Conversion.find(
        Conversion.project_id == project_id,
        Conversion.target_object == target_object,
    ).sort(+Conversion.planned_load_order).to_list()
    convs = [x for x in convs if x.template_id and
             (x.source_dataset_ids or getattr(x, "source_type", "") == "ebs")]
    return convs[0] if convs else None


async def _run_merged_generation(project_id, target_object, fmt, include_header, carrier_id) -> None:
    """Background worker: build + merge + write the one-file-per-interface output off
    the request thread (wide multi-source objects would otherwise blow the gateway)."""
    from app.services.output_service import generate_merged_artifact
    try:
        await generate_merged_artifact(project_id, target_object, fmt=fmt, include_header=include_header)
        c = await Conversion.get(carrier_id)
        if c:
            await c.set({"output_status": "ready", "output_error": None, "updated_at": datetime.utcnow()})
    except Exception as exc:  # noqa: BLE001
        try:
            c = await Conversion.get(carrier_id)
            if c:
                await c.set({"output_status": "failed", "output_error": str(exc)[:500]})
        except Exception:
            pass


@output_router.post("/{conversion_id}/generate-merged")
async def generate_merged(
    conversion_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx|template)$"),
    include_header: bool | None = Query(None),
    wait: bool = Query(False),
    _: User = Depends(get_current_user),
):
    """Generate ONE merged file for this conversion's interface object — merging all
    per-source conversions (merged + de-duplicated + cleansed + validated). Async by
    default: returns immediately with the CARRIER conversion id to poll via
    /generation-status; the artifact is stored under the carrier. Pass wait=true to
    block (used internally)."""
    import asyncio
    c = await _require_conversion(conversion_id)
    obj = c.target_object or ""
    carrier = await _carrier_for_object(c.project_id, obj) or c
    if wait:
        from app.services.output_service import generate_merged_artifact
        art = await generate_merged_artifact(c.project_id, obj, fmt=fmt, include_header=include_header)
        return {"status": "ready", "id": str(art.id), "conversion_id": str(art.conversion_id),
                "file_name": art.output_file_name, "row_count": art.row_count,
                "column_count": art.column_count, "dq_report": getattr(art, "dq_report", None)}
    await carrier.set({"output_status": "generating", "output_error": None,
                       "output_started_at": datetime.utcnow()})
    asyncio.create_task(_run_merged_generation(c.project_id, obj, fmt, include_header, carrier.id))
    return {"status": "generating", "conversion_id": str(carrier.id), "carrier_id": str(carrier.id)}


async def _run_merged_all(project_id, fmt, include_header, jobs) -> None:
    """Background: generate the merged file for every interface object, sequentially
    (bounded memory), updating each carrier's status as it completes."""
    from app.services.output_service import generate_merged_artifact
    for obj, carrier_id in jobs:
        try:
            await generate_merged_artifact(project_id, obj, fmt=fmt, include_header=include_header)
            c = await Conversion.get(carrier_id)
            if c:
                await c.set({"output_status": "ready", "output_error": None, "updated_at": datetime.utcnow()})
        except Exception as exc:  # noqa: BLE001
            try:
                c = await Conversion.get(carrier_id)
                if c:
                    await c.set({"output_status": "failed", "output_error": str(exc)[:500]})
            except Exception:
                pass


@output_router.post("/project/{project_id}/generate-merged-all")
async def generate_merged_all(
    project_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx|template)$"),
    include_header: bool | None = Query(None),
    _: User = Depends(get_current_user),
):
    """Kick off merged generation for EVERY interface object in the project, in the
    background. Returns the carrier conversion per object to poll via
    /generation-status; then GET /download-all zips the (already-generated) merged
    files fast. This keeps wide multi-source projects off the gateway timeout."""
    import asyncio
    convs = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)).to_list()
    by_obj: dict[str, list] = {}
    for c in convs:
        is_ebs = getattr(c, "source_type", "dataset") == "ebs"
        if not c.template_id or (not is_ebs and not c.dataset_id):
            continue
        by_obj.setdefault(c.target_object or c.name, []).append(c)
    objs = sorted(by_obj, key=lambda o: min((cc.planned_load_order or 100) for cc in by_obj[o]))
    jobs, carriers = [], []
    for obj in objs:
        carrier = sorted(by_obj[obj], key=lambda x: x.planned_load_order or 100)[0]
        await carrier.set({"output_status": "generating", "output_error": None,
                           "output_started_at": datetime.utcnow()})
        jobs.append((obj, carrier.id))
        carriers.append({"object": obj, "conversion_id": str(carrier.id)})
    if not jobs:
        raise HTTPException(400, "No bound conversions to generate")
    asyncio.create_task(_run_merged_all(project_id, fmt, include_header, jobs))
    return {"status": "generating", "objects": len(jobs), "carriers": carriers}


@output_router.get("/{conversion_id}/outputs", response_model=list[ConvertedOutputOut])
async def list_outputs(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    outputs = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-generated_at").to_list()
    return [
        {**o.model_dump(), "id": str(o.id), "conversion_id": str(o.conversion_id)}
        for o in outputs
    ]


@output_router.get("/{conversion_id}/download-output")
async def download_output(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    out = await ConvertedOutput.find(
        ConvertedOutput.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-generated_at").first_or_none()
    if not out or not Path(out.output_file_path).exists():
        raise HTTPException(404, "No output artifact found — generate output first")
    if getattr(out, "status", "") == "stale":
        # Same reasoning as download-all: handing back a file the rules have moved on
        # from is worse than refusing, because it looks like the change did not work.
        raise HTTPException(
            409,
            "This output was generated before the current rules and no longer matches "
            "them — regenerate it before downloading.")
    return FileResponse(out.output_file_path, filename=out.output_file_name)


def _safe_name(s: str) -> str:
    """Filesystem-safe token for zip entry names."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "conversion"


@output_router.get("/project/{project_id}/download-all")
async def download_all_outputs(
    project_id: str,
    fmt: str = Query("csv", pattern="^(csv|xlsx|template)$"),
    regenerate: bool = Query(False),
    _: User = Depends(get_current_user),
):
    """Zip the merged FBDI output for every interface object in a project — one
    merged, de-duplicated, cleansed, validated file per interface (NOT one per
    source). By default this REUSES the merged file already generated per interface
    (a fast zip); regenerating wide multi-source objects here would blow the gateway
    timeout, so the client should call POST /generate-merged-all first (background)
    and poll, then download. Pass ?regenerate=true to force a rebuild inline."""
    from app.models.project import Project

    project = await Project.get(PydanticObjectId(project_id))
    if not project:
        raise HTTPException(404, "Project not found")

    conversions = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).sort(+Conversion.planned_load_order).to_list()
    if not conversions:
        raise HTTPException(404, "This engagement has no conversions yet")

    # Group by target interface object so MULTIPLE source files for the same object
    # (e.g. eBOS + NetSuite supplier) become ONE merged, de-duplicated, cleansed,
    # validated file per interface — not one file per source. Order by the object's
    # load sequence.
    from app.services.output_service import generate_merged_artifact
    skipped: list[str] = []
    by_obj: dict[str, list] = {}
    for c in conversions:
        is_ebs = getattr(c, "source_type", "dataset") == "ebs"
        if not c.template_id or (not is_ebs and not c.dataset_id):
            skipped.append(c.name)
            continue
        obj = c.target_object or c.name
        by_obj.setdefault(obj, []).append(c)
    objs = sorted(by_obj, key=lambda o: min((cc.planned_load_order or 100) for cc in by_obj[o]))

    buf = io.BytesIO()
    used: dict[str, int] = {}
    added = 0
    stale: list[str] = []
    ext = "xlsx" if fmt == "xlsx" else "csv"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, obj in enumerate(objs, start=1):
            group = by_obj[obj]
            art = None
            # REUSE the merged artifact already written for this object. The merged
            # file is written under a carrier conversion, but which conversion is the
            # carrier can differ by sort tie-breaks, so look across EVERY conversion
            # in the group and take the newest artifact whose file is on disk. NOTE:
            # a "csv" generation may be a .zip (Oracle FBDI bundle) or a .csv — both
            # are the csv family; a filled "template" is a .xlsm. Only reuse an
            # artifact whose extension belongs to the requested format's family.
            _FAMILY = {"csv": {"csv", "zip"}, "xlsx": {"xlsx"},
                       "template": {"xlsm", "xlsx"}}
            _want_ext = _FAMILY.get(fmt, {"csv", "zip"})
            if not regenerate:
                cand = None
                for cc in group:
                    e = await ConvertedOutput.find(
                        ConvertedOutput.conversion_id == cc.id
                    ).sort("-generated_at").first_or_none()
                    if not e or not Path(e.output_file_path).exists():
                        continue
                    # STALE MEANS THE RULES CHANGED SINCE THIS FILE WAS WRITTEN.
                    # Reusing it hands back the pre-fix output and every symptom
                    # follows: a rule edited in the UI never appears in the download,
                    # a Fix button reports success and changes nothing, and a change
                    # one analyst makes is invisible to a colleague — because BOTH are
                    # served the same cached artifact from disk. Staleness was recorded
                    # everywhere and consulted nowhere. Treat it exactly like a missing
                    # file: fall through and rebuild.
                    if getattr(e, "status", "") == "stale":
                        stale.append(f"{obj} ({cc.name})")
                        continue
                    ext_e = Path(e.output_file_name).suffix.lstrip(".").lower()
                    if ext_e not in _want_ext:
                        continue  # wrong format family
                    if cand is None or (e.generated_at and cand.generated_at
                                        and e.generated_at > cand.generated_at):
                        cand = e
                art = cand
            if art is None:
                # MISSING is not the same as STALE. ``regenerate`` means "rebuild
                # even though a good file exists"; it must not mean "silently omit
                # an object that has no file in this format yet". The 28-Jul
                # "FBDI templates" download shipped only 2 of the 7 supplier
                # interfaces — the other 5 had csv-family artifacts from the
                # earlier CSV run but no xlsx/xlsm, so they fell into this branch
                # and vanished from a bundle that gave no hint it was incomplete.
                # A partial load file set is worse than a slow download.
                try:
                    art = await generate_merged_artifact(project_id, obj, fmt=fmt)
                except Exception:
                    skipped.append(obj)
                    continue
            if not art or not Path(art.output_file_path).exists():
                skipped.append(obj)
                continue
            real_ext = (Path(art.output_file_name).suffix.lstrip(".") or ext)
            order = min((cc.planned_load_order for cc in group
                         if cc.planned_load_order and cc.planned_load_order < 100), default=None)
            prefix = f"{order:02d}_" if order is not None else ""
            arcname = f"{prefix}{_safe_name(obj)}.{real_ext}"
            if arcname in used:
                used[arcname] += 1
                arcname = f"{prefix}{_safe_name(obj)}_{used[arcname]}.{real_ext}"
            else:
                used[arcname] = 0
            zf.write(art.output_file_path, arcname=arcname)
            added += 1

    if added == 0:
        if stale:
            # Files just aren't generated yet — tell the client to generate first.
            raise HTTPException(
                409,
                "Merged files aren't generated yet. Generate the merged output for "
                "each interface first (Download all triggers this automatically), "
                "then download.",
            )
        raise HTTPException(
            400,
            "No conversions in this engagement are ready to generate FBDI output "
            "(each needs a source dataset and a bound FBDI template).",
        )

    buf.seek(0)
    zip_name = f"{_safe_name(project.name)}_FBDI.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"',
            "X-Files-Added": str(added),
            "X-Files-Skipped": str(len(skipped)),
            # A load-file bundle that is missing interfaces is dangerous in a way a
            # failed download is not, so make incompleteness machine-readable
            # instead of leaving the caller to count the entries.
            "X-Files-Expected": str(len(objs)),
            "X-Objects-Skipped": ", ".join(skipped)[:400],
        },
    )


@output_router.get("/project/{project_id}/download-zip")
async def download_project_zip(
    project_id: str,
    _: User = Depends(get_current_user),
):
    """Package the LATEST already-generated FBDI output for every conversion in
    the project into one zip — no mapping or generation. The client calls this
    after it has auto-mapped + generated each object itself (so it can show
    per-object progress); this step just zips what's on disk, fast."""
    from app.models.project import Project

    project = await Project.get(PydanticObjectId(project_id))
    if not project:
        raise HTTPException(404, "Project not found")
    conversions = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).sort(+Conversion.planned_load_order).to_list()
    if not conversions:
        raise HTTPException(404, "This engagement has no conversions yet")

    buf = io.BytesIO()
    used: dict[str, int] = {}
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in conversions:
            art = await ConvertedOutput.find(
                ConvertedOutput.conversion_id == c.id
            ).sort("-generated_at").first_or_none()
            if not art or not art.output_file_path or not Path(art.output_file_path).exists():
                continue
            real_ext = (Path(art.output_file_name or "").suffix.lstrip(".") or "csv")
            order = c.planned_load_order if c.planned_load_order and c.planned_load_order < 100 else None
            prefix = f"{order:02d}_" if order is not None else ""
            arcname = f"{prefix}{_safe_name(c.name)}.{real_ext}"
            if arcname in used:
                used[arcname] += 1
                arcname = f"{prefix}{_safe_name(c.name)}_{used[arcname]}.{real_ext}"
            else:
                used[arcname] = 0
            zf.write(art.output_file_path, arcname=arcname)
            added += 1

    if added == 0:
        raise HTTPException(400, "No generated outputs to package — generate output first.")
    buf.seek(0)
    zip_name = f"{_safe_name(project.name)}_FBDI.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_name}"',
            "X-Files-Added": str(added),
        },
    )


# ----- LOAD -----
load_router = APIRouter(prefix="/api", tags=["load"])


_LOAD_ERROR_PATTERNS = [
    ("value.*not.*(valid|found|lookup|value set)", "The value isn't in Oracle's lookup (LOV) for this field.",
     "Add a value crosswalk to the accepted code, or correct the source value."),
    ("mandatory|required|cannot be null|missing", "A required field was empty for this row.",
     "Map a source column or set a default for the field before reloading."),
    ("exceed|too long|maximum length|size", "A value is longer than the column allows.",
     "Trim/abbreviate the value to the field's max length."),
    ("duplicate|already exists|unique", "A duplicate key already exists in Oracle.",
     "De-duplicate the source or switch Import Action to UPDATE."),
    ("date|format", "A value isn't in the format Oracle expects.",
     "Reformat the value (e.g. dates to YYYY/MM/DD) and reload."),
    ("parent|reference|foreign|dependinquiry|not found", "A referenced parent record doesn't exist yet.",
     "Load the parent object first (respect the load-order dependencies)."),
]


@load_router.post("/load-runs/{load_run_id}/explain-errors")
async def explain_load_errors(load_run_id: str, _: User = Depends(get_current_user)):
    """Fill plain-English root cause + suggested fix for a load run's errors that
    don't have them yet (pattern-based; reliable). Returns the explained errors."""
    import re as _re
    errs = await LoadError.find(LoadError.load_run_id == PydanticObjectId(load_run_id)).to_list()
    explained = 0
    for e in errs:
        if e.root_cause and e.suggested_fix:
            continue
        msg = (e.error_message or "").lower()
        rc, fix = None, None
        for pat, cause, suggestion in _LOAD_ERROR_PATTERNS:
            if _re.search(pat, msg):
                rc, fix = cause, suggestion
                break
        if not rc:
            rc = "Oracle rejected this row during import."
            fix = "Review the error message and the row's values against the field's requirements."
        await e.set({"root_cause": e.root_cause or rc, "suggested_fix": e.suggested_fix or fix})
        explained += 1
    return {"load_run_id": load_run_id, "explained": explained, "total_errors": len(errs)}


@load_router.post("/conversions/{conversion_id}/simulate-load", response_model=LoadRunOut)
async def simulate_load_endpoint(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    run = await simulate_conversion_load(c)
    return {**run.model_dump(), "id": str(run.id), "conversion_id": str(run.conversion_id)}


@load_router.get("/conversions/{conversion_id}/load-runs", response_model=list[LoadRunOut])
async def list_load_runs(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    runs = await LoadRun.find(
        LoadRun.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-started_at").to_list()
    return [
        {**r.model_dump(), "id": str(r.id), "conversion_id": str(r.conversion_id)}
        for r in runs
    ]


@load_router.get("/load-runs/{run_id}/errors", response_model=list[LoadErrorOut])
async def list_load_errors(
    run_id: str,
    _: User = Depends(get_current_user),
):
    errors = await LoadError.find(
        LoadError.load_run_id == PydanticObjectId(run_id)
    ).to_list()
    return [
        {**e.model_dump(), "id": str(e.id), "load_run_id": str(e.load_run_id)}
        for e in errors
    ]


@load_router.post("/load-runs/{run_id}/ai-explain", response_model=list[LoadErrorOut])
async def ai_explain_load_errors(run_id: str, _: User = Depends(get_current_user)):
    """Re-explain an existing run's errors with AI: fills each LoadError's
    root_cause + suggested_fix with a plain-English cause and concrete fix."""
    run = await LoadRun.get(PydanticObjectId(run_id))
    if not run:
        raise HTTPException(404, "Load run not found")
    errors = await LoadError.find(LoadError.load_run_id == run.id).to_list()
    if not errors:
        return []
    conv = await Conversion.get(run.conversion_id)
    # Aliased: this module also defines an ENDPOINT called explain_load_errors, and
    # an unaliased import shadows it here. It resolves correctly today only because
    # the import is function-local — one refactor that hoists it silently swaps a
    # service call for an HTTP handler call.
    from app.services.ai_error_service import explain_load_errors as _ai_explain
    dicts = [e.model_dump() for e in errors]
    enriched = await _ai_explain(dicts, conv.target_object if conv else None)
    by_msg = {(d.get("error_message") or ""): d for d in enriched}
    out = []
    for e in errors:
        d = by_msg.get(e.error_message or "")
        if d and (d.get("root_cause") != e.root_cause or d.get("suggested_fix") != e.suggested_fix):
            await e.set({"root_cause": d.get("root_cause"), "suggested_fix": d.get("suggested_fix")})
        out.append({**e.model_dump(), "id": str(e.id), "load_run_id": str(e.load_run_id)})
    return out


@load_router.get(
    "/conversions/{conversion_id}/load-errors", response_model=list[LoadErrorOut]
)
async def list_latest_load_errors(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    latest = await LoadRun.find(
        LoadRun.conversion_id == PydanticObjectId(conversion_id)
    ).sort("-started_at").first_or_none()
    if not latest:
        return []
    errors = await LoadError.find(
        LoadError.load_run_id == latest.id
    ).to_list()
    return [
        {**e.model_dump(), "id": str(e.id), "load_run_id": str(e.load_run_id)}
        for e in errors
    ]


@load_router.get("/conversions/{conversion_id}/load-summary", response_model=LoadSummaryOut)
async def load_summary(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    return await build_load_summary(c)


# ----- WORKFLOW -----
workflow_router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@workflow_router.post("", response_model=WorkflowOut)
async def create_workflow(
    payload: WorkflowCreate,
    _: User = Depends(get_current_user),
):
    w = Workflow(**payload.model_dump(), status="saved")
    await w.insert()
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.get("", response_model=list[WorkflowOut])
async def list_workflows(_: User = Depends(get_current_user)):
    workflows = await Workflow.find_all().sort("-updated_at").to_list()
    return [
        {**w.model_dump(), "id": str(w.id),
         "conversion_id": str(w.conversion_id) if w.conversion_id else None}
        for w in workflows
    ]


@workflow_router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: str,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str,
    payload: WorkflowUpdate,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    await w.set(payload.model_dump(exclude_unset=True))
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


@workflow_router.post("/{workflow_id}/run", response_model=WorkflowOut)
async def run_workflow(
    workflow_id: str,
    _: User = Depends(get_current_user),
):
    w = await Workflow.get(PydanticObjectId(workflow_id))
    if not w:
        raise HTTPException(404, "Workflow not found")
    conv = await Conversion.get(w.conversion_id) if w.conversion_id else None
    summary: dict = {"steps": [], "started_at": datetime.utcnow().isoformat()}
    await w.set({"status": "running"})

    try:
        for node in (w.nodes or []):
            ntype = (node.get("data") or {}).get("nodeType") or node.get("type") or "unknown"
            step = {"node_id": node.get("id"), "type": ntype, "status": "ok", "detail": None}
            if not conv:
                step["status"] = "skipped"
                step["detail"] = "no conversion bound to dataflow"
                summary["steps"].append(step)
                continue
            try:
                if ntype == "ai_auto_map":
                    _ebs = getattr(conv, "source_type", "dataset") == "ebs"
                    if not conv.template_id or (not _ebs and not conv.dataset_id):
                        raise RuntimeError("conversion not fully bound")
                    from app.services.mapping_service import run_mapping_suggestions
                    res = await run_mapping_suggestions(conv)
                    step["detail"] = f"{len(res)} mapping suggestions"
                elif ntype == "validate":
                    from app.services.quality_service import run_validation
                    res = await run_validation(conv)
                    step["detail"] = f"{len(res)} validation issues"
                elif ntype == "preview_output":
                    res = await get_output_preview(conv, limit=10)
                    step["detail"] = f"{res['total_rows']} converted rows"
                elif ntype == "load_to_fusion":
                    res = await simulate_conversion_load(conv)
                    step["detail"] = (
                        f"passed={res.passed_count} failed={res.failed_count} "
                        f"warnings={res.warning_count}"
                    )
                else:
                    step["detail"] = "node executed"
            except Exception as e:
                step["status"] = "error"
                step["detail"] = str(e)
            summary["steps"].append(step)

        summary["completed_at"] = datetime.utcnow().isoformat()
        new_status = "success" if all(s["status"] != "error" for s in summary["steps"]) else "failed"
    except Exception as e:
        new_status = "failed"
        summary["error"] = str(e)
        summary["completed_at"] = datetime.utcnow().isoformat()

    await w.set({
        "status": new_status,
        "last_run_at": datetime.utcnow(),
        "last_run_summary": summary,
    })
    return {
        **w.model_dump(), "id": str(w.id),
        "conversion_id": str(w.conversion_id) if w.conversion_id else None,
    }


# ----- DEPENDENCY -----
dep_router = APIRouter(prefix="/api/dependencies", tags=["dependencies"])


@dep_router.get("", response_model=list[DependencyOut])
async def list_dependencies(_: User = Depends(get_current_user)):
    deps = await Dependency.find_all().to_list()
    return [{**d.model_dump(), "id": str(d.id)} for d in deps]


@dep_router.get("/impact/{conversion_id}")
async def conversion_dependency_impact(
    conversion_id: str,
    _: User = Depends(get_current_user),
):
    c = await _require_conversion(conversion_id)
    tpl = await (
        __import__("app.models.fbdi", fromlist=["FBDITemplate"]).FBDITemplate.get(c.template_id)
    ) if c.template_id else None
    target_obj = (tpl.business_object if tpl else (c.target_object or "")).lower()
    deps = await Dependency.find_all().to_list()
    relevant = [
        d for d in deps
        if target_obj in d.target_object.lower() or target_obj in d.source_object.lower()
    ]
    summary = await build_load_summary(c)
    return {
        "object": (tpl.business_object if tpl else c.target_object),
        "dependencies": [
            {
                "source_object": d.source_object,
                "target_object": d.target_object,
                "relationship_type": d.relationship_type,
                "description": d.description,
            }
            for d in relevant
        ],
        "impacts": summary["dependency_impacts"],
    }


# ----- DASHBOARD -----
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@dashboard_router.get("/kpis", response_model=DashboardKpis)
async def kpis(_: User = Depends(get_current_user)):
    return await get_kpis()
