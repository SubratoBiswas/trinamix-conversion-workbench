"""FBDI template endpoints."""
from pathlib import Path
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate, FBDITemplateFile
from app.models.user import User
from app.parsers.fbdi_parser import parse_fbdi_template
from app.schemas.fbdi import FBDIFieldOut, FBDIFieldUpdate, FBDISheetOut, FBDITemplateDetailOut, FBDITemplateOut
from app.services.auth_service import get_current_user
from app.services.fbdi_service import create_template_from_upload, materialize_template_file

router = APIRouter(prefix="/api/fbdi", tags=["fbdi"])


def _fld_out(f: FBDIField) -> dict:
    d = f.model_dump()
    d["id"] = str(f.id)
    d["template_id"] = str(f.template_id)
    d["sheet_id"] = str(f.sheet_id)
    return d


async def _detail_payload(tpl: FBDITemplate) -> dict:
    sheets = await FBDISheet.find(FBDISheet.template_id == tpl.id).sort("sequence").to_list()
    fields = await FBDIField.find(FBDIField.template_id == tpl.id).sort("sequence").to_list()
    d = tpl.model_dump()
    d["id"] = str(tpl.id)
    d["sheets"] = [{"id": str(s.id), "template_id": str(s.template_id), **{k: v for k, v in s.model_dump().items() if k not in ("id","template_id")}} for s in sheets]
    d["fields"] = [_fld_out(f) for f in fields]
    return d


@router.post("/upload", response_model=FBDITemplateDetailOut)
async def upload_template(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    module: str | None = Form(None),
    business_object: str | None = Form(None),
    _: User = Depends(get_current_user),
):
    try:
        tpl = await create_template_from_upload(file, name, module, business_object)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return await _detail_payload(tpl)


@router.post("/customer/repoint")
async def repoint_customer_conversions(_: User = Depends(get_current_user)):
    """Manual trigger for the Customer template repair (also runs automatically at
    startup). Fast + idempotent: force-seeds the real 19-sheet Customer Import if
    missing and re-points flat-template conversions onto it, clearing their stale
    mappings so Re-run AI rebuilds against the real sheets. No inline AI mapping,
    so it can't time out."""
    from app.services.template_seed_service import ensure_customer_multisheet
    return await ensure_customer_multisheet()


@router.get("/lookups/status")
async def lookups_status(_: User = Depends(get_current_user)):
    """Which Oracle lookup types the loaded templates need, and which we hold codes for."""
    from app.services.lookup_import_service import lookup_status
    return await lookup_status()


@router.post("/lookups/import")
async def lookups_import(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
):
    """Import lookup codes from a Manage Standard Lookups export.

    These are the ONLY authoritative source for the lookup types the FBDI templates
    reference but don't publish — the codes are configured per Fusion instance. Once
    imported they're written onto the matching template fields, which flips those
    columns from "passing values through unvalidated" to fully mapped.
    """
    import tempfile

    from app.services.lookup_import_service import import_lookup_codes

    suffix = Path(file.filename or "lookups.csv").suffix.lower() or ".csv"
    if suffix not in (".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"):
        raise HTTPException(400, "Upload a CSV or Excel export of Manage Standard Lookups.")

    contents = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        tmp_path = tmp.name

    try:
        result = await import_lookup_codes(
            tmp_path, file_type=suffix.lstrip("."), user_email=user.email,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if result.get("error"):
        raise HTTPException(400, result["error"])
    return result


@router.get("/templates", response_model=list[FBDITemplateOut])
async def list_templates(include_retired: bool = False,
                         _: User = Depends(get_current_user)):
    """Every template a conversion can be bound to.

    RETIRED templates are hidden. A duplicate that claimed the same object as the real
    one — "Worker HCM" alongside the six-object Employee HDL template — is what let a
    conversion be pointed at a two-sheet template and emit two tabs, with nothing on
    screen saying which of the two it was using. Retiring it is only half a fix while
    it is still offered in the picker: the next conversion picks it again.

    It is retired rather than deleted because outputs and mapping rows reference
    template_id, so `include_retired=true` still returns it for anything that needs to
    explain an artifact generated before the cleanup.
    """
    templates = await FBDITemplate.find_all().sort("-uploaded_at").to_list()
    if not include_retired:
        templates = [t for t in templates if (t.status or "") != "retired"]
    return [{"id": str(t.id), **{k: v for k, v in t.model_dump().items() if k != "id"}} for t in templates]


@router.get("/templates/{template_id}", response_model=FBDITemplateDetailOut)
async def get_template(template_id: str, _: User = Depends(get_current_user)):
    tpl = await FBDITemplate.get(PydanticObjectId(template_id))
    if not tpl:
        raise HTTPException(404, "Template not found")
    return await _detail_payload(tpl)


@router.get("/templates/{template_id}/fields", response_model=list[FBDIFieldOut])
async def list_template_fields(template_id: str, _: User = Depends(get_current_user)):
    tid = PydanticObjectId(template_id)
    # Interface sheet name per field, so the UI can disambiguate fields that share
    # a name across sheets (e.g. "Item Number" is the linking key on all 16 item
    # interface tables — without this it looks like the same field repeated).
    _sname = {s.id: s.sheet_name for s in
              await FBDISheet.find(FBDISheet.template_id == tid).to_list()}
    fields = await FBDIField.find(FBDIField.template_id == tid).sort("sequence").to_list()
    out = []
    for f in fields:
        d = _fld_out(f)
        d["sheet_name"] = _sname.get(f.sheet_id)
        out.append(d)
    return out


@router.get("/templates/{template_id}/synthetic-data")
async def synthetic_data(
    template_id: str,
    rows: int = 25,
    fmt: str = "csv",
    _: User = Depends(get_current_user),
):
    """Generate synthetic, type-valid sample data for this interface (for a load
    rehearsal or demo, without real client data). Honours required flags, data
    types, max length and published lists-of-values. Returns a CSV (single sheet),
    a .zip of CSVs (multi-sheet), or an .xlsx workbook (fmt=xlsx)."""
    import io
    import re as _re
    import zipfile
    from fastapi.responses import StreamingResponse
    import pandas as pd
    from app.services.synthetic_data_service import synthetic_frame

    rows = max(1, min(1000, rows))
    fmt = (fmt or "csv").lower()
    tid = PydanticObjectId(template_id)
    tpl = await FBDITemplate.get(tid)
    if not tpl:
        raise HTTPException(404, "Template not found")
    sheets = await FBDISheet.find(FBDISheet.template_id == tid).sort("sequence").to_list()
    fields = await FBDIField.find(FBDIField.template_id == tid).sort("sequence").to_list()
    if not fields:
        raise HTTPException(400, "Template has no parsed fields to generate from")
    by_sheet: dict = {}
    for f in fields:
        by_sheet.setdefault(f.sheet_id, []).append(f)

    def _fd(f):
        return {"field_name": f.field_name, "display_name": f.display_name,
                "required": f.required, "data_type": f.data_type,
                "max_length": f.max_length, "allowed_values": f.allowed_values,
                "format_mask": f.format_mask}

    def _safe(s):
        return _re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "sheet")).strip("_") or "sheet"

    ordered = [s for s in sheets if s.id in by_sheet] or [None]
    obj = _safe(tpl.business_object or tpl.name or "fbdi")

    if fmt == "xlsx":
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as xw:
            for s in ordered:
                sid = s.id if s else next(iter(by_sheet))
                df = synthetic_frame([_fd(f) for f in by_sheet[sid]], rows)
                nm = _safe(s.sheet_name if s else obj)[:31] or "Sheet1"
                df.to_excel(xw, index=False, sheet_name=nm)
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{obj}_synthetic.xlsx"'})

    if len(ordered) == 1:
        sid = ordered[0].id if ordered[0] else next(iter(by_sheet))
        df = synthetic_frame([_fd(f) for f in by_sheet[sid]], rows)
        data = df.to_csv(index=False)
        return StreamingResponse(iter([data]), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{obj}_synthetic.csv"'})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, s in enumerate(ordered, 1):
            df = synthetic_frame([_fd(f) for f in by_sheet[s.id]], rows)
            zf.writestr(f"{i:02d}_{_safe(s.sheet_name)}.csv", df.to_csv(index=False))
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{obj}_synthetic.zip"'})


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: str, _: User = Depends(get_current_user)):
    tpl = await FBDITemplate.get(PydanticObjectId(template_id))
    if not tpl:
        raise HTTPException(404, "Template not found")
    await FBDIField.find(FBDIField.template_id == tpl.id).delete()
    await FBDISheet.find(FBDISheet.template_id == tpl.id).delete()
    await FBDITemplateFile.find(FBDITemplateFile.template_id == tpl.id).delete()
    await tpl.delete()


@router.get("/debug-parser")
async def debug_parser(_: User = Depends(get_current_user)):
    """Test parser with an in-memory xlsx. Call this after deploy to verify the parser works on Render."""
    import io, tempfile, os
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "TEST_SHEET"
    ws.append(["* FIELD_A", "FIELD_B", "* FIELD_C"])
    ws.append(["value1", "value2", "value3"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
        f.write(buf.read())
        tmp = f.name
    try:
        result = parse_fbdi_template(tmp)
        return {
            "parser_ok": len(result["fields"]) > 0,
            "field_count": len(result["fields"]),
            "fields": [{"name": f["field_name"], "required": f["required"]} for f in result["fields"]],
            "sheets": result["sheets"],
        }
    except Exception as exc:
        return {"parser_ok": False, "error": str(exc), "type": type(exc).__name__}
    finally:
        os.unlink(tmp)


@router.post("/templates/{template_id}/reparse")
async def reparse_template(template_id: str, _: User = Depends(get_current_user)):
    """Re-parse the stored file for a template and refresh its fields/sheets in DB."""
    tpl = await FBDITemplate.get(PydanticObjectId(template_id))
    if not tpl:
        raise HTTPException(404, "Template not found")
    src = await materialize_template_file(tpl)
    if src is None:
        raise HTTPException(422, "Stored file not found; please delete and re-upload this template")
    # Delete existing fields and sheets
    await FBDIField.find(FBDIField.template_id == tpl.id).delete()
    await FBDISheet.find(FBDISheet.template_id == tpl.id).delete()
    # Re-parse
    parsed = parse_fbdi_template(str(src))
    sheet_id_by_name: dict[str, object] = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name=s["sheet_name"],
            sequence=s["sequence"],
            field_count=s["field_count"],
        )
        await sheet.insert()
        sheet_id_by_name[s["sheet_name"]] = sheet.id
    for f in parsed["fields"]:
        sheet_name = f.pop("sheet_name", None)
        sheet_id = sheet_id_by_name.get(sheet_name)
        if sheet_id is None:
            continue
        await FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f).insert()
    req_count = sum(1 for f in parsed["fields"] if f.get("required"))
    await tpl.set({
        "status": "parsed" if parsed["fields"] else "manual",
        "required_field_count": req_count,
    })
    return await _detail_payload(tpl)


@router.post("/reparse-all")
async def reparse_all_templates(_: User = Depends(get_current_user)):
    """Re-parse all templates that have a stored file on disk."""
    templates = await FBDITemplate.find_all().to_list()
    results = []
    for tpl in templates:
        src = await materialize_template_file(tpl)
        if src is None:
            results.append({"id": str(tpl.id), "name": tpl.name, "status": "skipped_no_file", "fields": 0})
            continue
        await FBDIField.find(FBDIField.template_id == tpl.id).delete()
        await FBDISheet.find(FBDISheet.template_id == tpl.id).delete()
        parsed = parse_fbdi_template(str(src))
        sheet_id_by_name: dict[str, object] = {}
        for s in parsed["sheets"]:
            sheet = FBDISheet(
                template_id=tpl.id,
                sheet_name=s["sheet_name"],
                sequence=s["sequence"],
                field_count=s["field_count"],
            )
            await sheet.insert()
            sheet_id_by_name[s["sheet_name"]] = sheet.id
        for f in parsed["fields"]:
            sheet_name = f.pop("sheet_name", None)
            sheet_id = sheet_id_by_name.get(sheet_name)
            if sheet_id is None:
                continue
            await FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f).insert()
        new_status = "parsed" if parsed["fields"] else "manual"
        req_count = sum(1 for f in parsed["fields"] if f.get("required"))
        await tpl.set({"status": new_status, "required_field_count": req_count})
        results.append({"id": str(tpl.id), "name": tpl.name, "status": new_status, "fields": len(parsed["fields"])})
    return {"reparsed": len(results), "results": results}


@router.put("/fields/{field_id}", response_model=FBDIFieldOut)
async def update_field(
    field_id: str, payload: FBDIFieldUpdate, _: User = Depends(get_current_user)
):
    f = await FBDIField.get(PydanticObjectId(field_id))
    if not f:
        raise HTTPException(404, "Field not found")
    await f.set(payload.model_dump(exclude_unset=True))
    return _fld_out(f)
