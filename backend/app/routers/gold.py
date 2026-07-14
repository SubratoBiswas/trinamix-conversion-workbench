"""Gold-standard library — upload approved FBDI output without a project.

Mirrors the Templates section: a shared, project-independent library. Rules derived
here are global (keyed by target object), so every conversion of that object — past,
present and future — applies them at generate.
"""
from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.models.fbdi import GoldStandard
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.gold_library_service import create_gold_standard, library_summary, relearn

router = APIRouter(prefix="/api/gold", tags=["gold"])

_ALLOWED = (".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt")


def _out(g: GoldStandard) -> dict:
    return {
        "id": str(g.id),
        "name": g.name,
        "target_object": g.target_object,
        "template_id": str(g.template_id) if g.template_id else None,
        "template_name": g.template_name,
        "file_name": g.file_name,
        "size": g.size,
        "source_file_name": g.source_file_name,
        "match_confidence": g.match_confidence,
        "rows": g.rows,
        "defaults_learned": g.defaults_learned,
        "suppressed_learned": g.suppressed_learned,
        "mappings_learned": g.mappings_learned,
        "status": g.status,
        "note": g.note,
        "uploaded_by": g.uploaded_by,
        "uploaded_at": g.uploaded_at,
        "learned_at": g.learned_at,
    }


@router.get("")
async def list_gold(_: User = Depends(get_current_user)):
    golds = await GoldStandard.find_all().sort("-uploaded_at").to_list()
    return {"items": [_out(g) for g in golds], "summary": await library_summary()}


@router.post("/upload")
async def upload_gold(
    file: UploadFile = File(...),
    source_file: UploadFile | None = File(None),
    name: str | None = Form(None),
    template_id: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Upload an approved FBDI output. No project or conversion required.

    ``source_file`` is optional. Without it we learn constant defaults and the
    columns gold deliberately leaves blank — both derivable from the gold file
    alone. With it we can additionally infer source→target column mappings by
    value-set overlap, which is not something we're willing to guess at.
    """
    from pathlib import Path as _P

    if _P(file.filename or "").suffix.lower() not in _ALLOWED:
        raise HTTPException(400, "Upload an Excel or CSV copy of the approved FBDI output.")

    contents = await file.read()
    if not contents:
        raise HTTPException(400, "That file is empty.")

    src_bytes = None
    src_name = None
    if source_file is not None and source_file.filename:
        if _P(source_file.filename).suffix.lower() not in _ALLOWED:
            raise HTTPException(400, "The source extract must be an Excel or CSV file.")
        src_bytes = await source_file.read()
        src_name = source_file.filename

    gold = await create_gold_standard(
        file_name=file.filename or "gold.xlsx",
        contents=contents,
        name=name,
        template_id=template_id or None,
        source_file_name=src_name,
        source_contents=src_bytes or None,
        user_email=user.email,
    )
    return _out(gold)


@router.post("/{gold_id}/relearn")
async def relearn_gold(gold_id: str, _: User = Depends(get_current_user)):
    gold = await GoldStandard.get(PydanticObjectId(gold_id))
    if not gold:
        raise HTTPException(404, "Gold standard not found")
    result = await relearn(gold)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    fresh = await GoldStandard.get(PydanticObjectId(result["id"]))
    return _out(fresh) if fresh else result


@router.get("/{gold_id}/download")
async def download_gold(gold_id: str, _: User = Depends(get_current_user)):
    gold = await GoldStandard.get(PydanticObjectId(gold_id))
    if not gold or not gold.content:
        raise HTTPException(404, "File not stored")
    return Response(
        content=gold.content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{gold.file_name or "gold.xlsx"}"'},
    )


@router.delete("/{gold_id}")
async def delete_gold(gold_id: str, purge_rules: bool = False, _: User = Depends(get_current_user)):
    """Remove a gold file from the library.

    By default the rules it taught are KEPT — they may have been reviewed, edited,
    or reinforced by other gold files, and silently unlearning them would change
    every future conversion of the object without warning. Pass ``purge_rules=true``
    to also drop the rules this object learned from gold.
    """
    gold = await GoldStandard.get(PydanticObjectId(gold_id))
    if not gold:
        raise HTTPException(404, "Gold standard not found")

    purged = 0
    if purge_rules and gold.target_object:
        from app.models.learned import LearnedMapping
        others = await GoldStandard.find(
            GoldStandard.target_object == gold.target_object,
        ).to_list()
        if len([g for g in others if g.id != gold.id]) == 0:
            res = await LearnedMapping.find({
                "target_object": gold.target_object,
                "captured_from": "gold example",
            }).delete()
            purged = getattr(res, "deleted_count", 0) or 0

    await gold.delete()
    return {"deleted": True, "rules_purged": purged}
