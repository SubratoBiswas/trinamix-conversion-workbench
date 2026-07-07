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
