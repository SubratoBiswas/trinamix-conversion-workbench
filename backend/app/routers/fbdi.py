"""FBDI template endpoints."""
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.models.fbdi import FBDIField, FBDISheet, FBDITemplate
from app.models.user import User
from app.schemas.fbdi import FBDIFieldOut, FBDIFieldUpdate, FBDISheetOut, FBDITemplateDetailOut, FBDITemplateOut
from app.services.auth_service import get_current_user
from app.services.fbdi_service import create_template_from_upload

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
    await tpl.delete()


@router.put("/fields/{field_id}", response_model=FBDIFieldOut)
async def update_field(
    field_id: str, payload: FBDIFieldUpdate, _: User = Depends(get_current_user)
):
    f = await FBDIField.get(PydanticObjectId(field_id))
    if not f:
        raise HTTPException(404, "Field not found")
    await f.set(payload.model_dump(exclude_unset=True))
    return _fld_out(f)
