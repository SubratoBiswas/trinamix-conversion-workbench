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
async def repoint_customer_conversions(
    delete_flat: bool = False,
    user: User = Depends(get_current_user),
):
    """Move Customer conversions off a flat template onto the real 19-sheet FBDI.

    The tool can end up with two Customer templates: the real Oracle Customer Import
    (HZ_IMP_* interface tables, ~19 sheets) and a flat synthetic one (a single
    'Import' sheet from the built-in standard fields). A conversion pointed at the
    flat one produces a flat file no matter how good the gold/AI/learnings are —
    because those fill values, they don't change structure. This re-points such
    conversions to the real template and re-runs mapping so the fields line up.
    """
    from app.models.conversion import Conversion
    from app.services.mapping_service import run_mapping_suggestions

    # All customer templates, with their sheet counts.
    templates = await FBDITemplate.find_all().to_list()
    # Match the Customer OBJECT exactly — not "Customer Return" / "Credit Profile"
    # whose names merely contain "customer". Fall back to name only when a template
    # has no business object at all.
    def _is_customer(t) -> bool:
        bo = (t.business_object or "").strip().lower()
        if bo:
            return bo == "customer"
        return (t.name or "").strip().lower() in ("customer import", "customerimport")
    cust = [t for t in templates if _is_customer(t)]
    if not cust:
        raise HTTPException(404, "No customer templates found.")

    counts: dict = {}
    for t in cust:
        counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()

    real = max(cust, key=lambda t: counts.get(t.id, 0)) if cust else None

    # The real HZ_IMP Customer Import may never have loaded: a flat synthetic
    # namesake (business object "Customer", one "Import" sheet) already occupied the
    # name, so the idempotent seed SKIPPED the bundled 19-sheet file. If no
    # multi-sheet customer template exists, force-seed the bundled one from disk now
    # — that's what makes this button self-sufficient instead of erroring.
    seeded_now = False
    if real is None or counts.get(real.id, 0) < 5:
        from pathlib import Path as _P
        from app.services.template_seed_service import _DIR, _seed_one
        bundled = _P(_DIR) / "CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm"
        if not bundled.exists():
            raise HTTPException(
                422,
                "The real 19-sheet Customer Import isn't loaded and the bundled copy "
                "wasn't found on the server. Upload CustomerImport_HZ_IMP__RA_CUSTOMER.xlsm "
                "on this page, then retry.",
            )
        ok = await _seed_one(bundled, "Customer Import (HZ_IMP)", "Financials / Receivables", "Customer")
        if not ok:
            raise HTTPException(500, "Couldn't parse the bundled Customer Import template.")
        seeded_now = True
        # Re-read templates and recompute the real one (now the 19-sheet upload).
        templates = await FBDITemplate.find_all().to_list()
        cust = [t for t in templates
                if (t.business_object or "").strip().lower() == "customer"
                or "customer" in (t.name or "").lower()]
        for t in cust:
            counts[t.id] = await FBDISheet.find(FBDISheet.template_id == t.id).count()
        real = max(cust, key=lambda t: counts.get(t.id, 0))

    flat = [t for t in cust if t.id != real.id]
    flat_ids = {t.id for t in flat}

    repointed = 0
    remapped = 0
    conversions = await Conversion.find_all().to_list()
    for c in conversions:
        if c.template_id in flat_ids:
            await c.set({"template_id": real.id})
            repointed += 1
            try:
                res = await run_mapping_suggestions(c)
                remapped += len(res)
            except Exception:  # noqa: BLE001
                pass

    deleted = 0
    if delete_flat and repointed >= 0:
        for t in flat:
            # Only delete a genuinely flat template (few sheets) — never the real one.
            if counts.get(t.id, 0) < 5:
                await FBDISheet.find(FBDISheet.template_id == t.id).delete()
                await FBDIField.find(FBDIField.template_id == t.id).delete()
                await t.delete()
                deleted += 1

    return {
        "real_template": {"id": str(real.id), "name": real.name, "sheets": counts.get(real.id)},
        "seeded_real_template": seeded_now,
        "flat_templates_found": len(flat),
        "conversions_repointed": repointed,
        "mappings_regenerated": remapped,
        "flat_templates_deleted": deleted,
    }


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
async def list_templates(_: User = Depends(get_current_user)):
    templates = await FBDITemplate.find_all().sort("-uploaded_at").to_list()
    return [{"id": str(t.id), **{k: v for k, v in t.model_dump().items() if k != "id"}} for t in templates]


@router.get("/templates/{template_id}", response_model=FBDITemplateDetailOut)
async def get_template(template_id: str, _: User = Depends(get_current_user)):
    tpl = await FBDITemplate.get(PydanticObjectId(template_id))
    if not tpl:
        raise HTTPException(404, "Template not found")
    return await _detail_payload(tpl)


@router.get("/templates/{template_id}/fields", response_model=list[FBDIFieldOut])
async def list_template_fields(template_id: str, _: User = Depends(get_current_user)):
    fields = await FBDIField.find(
        FBDIField.template_id == PydanticObjectId(template_id)
    ).sort("sequence").to_list()
    return [_fld_out(f) for f in fields]


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
