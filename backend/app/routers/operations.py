"""Output, load, workflow, dependency, and dashboard endpoints."""
# deploy-nudge: re-trigger backend build for wave-2 (commit 8782692)
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

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
        try:
            c = await Conversion.get(PydanticObjectId(conversion_id))
            if c:
                await c.set({"output_status": "failed", "output_error": str(exc)[:500]})
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
        max_rows=max_rows)
    result["sources"] = names
    if use_ai:
        result = await ai_adjudicate_clusters(result)
    return result


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
                    ext_e = Path(e.output_file_name).suffix.lstrip(".").lower()
                    if ext_e not in _want_ext:
                        continue  # wrong format family
                    if cand is None or (e.generated_at and cand.generated_at
                                        and e.generated_at > cand.generated_at):
                        cand = e
                art = cand
            if art is None:
                if not regenerate:
                    # Nothing pre-generated for this object and we won't rebuild inline.
                    stale.append(obj)
                    continue
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
    from app.services.ai_error_service import explain_load_errors
    dicts = [e.model_dump() for e in errors]
    enriched = await explain_load_errors(dicts, conv.target_object if conv else None)
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
