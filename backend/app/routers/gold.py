"""Gold-standard library — upload approved FBDI output without a project.

Mirrors the Templates section: a shared, project-independent library. Rules derived
here are global (keyed by target object), so every conversion of that object — past,
present and future — applies them at generate.
"""
from __future__ import annotations

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.models.fbdi import GoldStandard
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.gold_library_service import (
    create_gold_standard, library_summary, orphan_rule_groups, relearn, repoint_gold,
)

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
    return {
        "items": [_out(g) for g in golds],
        # Objects whose gold rules are live but whose original file predates the
        # library (the old conversion-side upload learned from the file and then
        # deleted it). Shown so the learning isn't invisible just because we can't
        # produce the artefact.
        "orphans": await orphan_rule_groups(),
        "summary": await library_summary(),
    }


@router.post("/upload")
async def upload_gold(
    files: list[UploadFile] = File(...),
    source_file: UploadFile | None = File(None),
    name: str | None = Form(None),
    template_id: str | None = Form(None),
    user: User = Depends(get_current_user),
):
    """Upload one or more approved FBDI outputs. No project or conversion required.

    Multi-file is the normal case, not the exception: a supplier load is six gold
    files (supplier, addresses, sites, site assignments, contacts, banks) and each
    identifies its own template from its headers, so they can all go in at once.

    ``source_file`` is a SINGLE optional extract shared by every gold file in the
    batch — which is exactly right for a fan-out, where one legacy supplier extract
    is what produced all six outputs. Without it we learn constant defaults and the
    columns gold deliberately leaves blank (both derivable from the gold file alone).
    With it we can additionally infer source→target column mappings by value-set
    overlap, which is not something we're willing to guess at.

    ``name`` and ``template_id`` only apply when a single file is uploaded — with a
    batch, each file names itself and detects its own template.
    """
    from pathlib import Path as _P

    files = [f for f in files if f and f.filename]
    if not files:
        raise HTTPException(400, "No files uploaded.")

    for f in files:
        if _P(f.filename or "").suffix.lower() not in _ALLOWED:
            raise HTTPException(
                400, f"'{f.filename}' isn't an Excel or CSV file."
            )

    src_bytes = None
    src_name = None
    if source_file is not None and source_file.filename:
        if _P(source_file.filename).suffix.lower() not in _ALLOWED:
            raise HTTPException(400, "The source extract must be an Excel or CSV file.")
        src_bytes = await source_file.read()
        src_name = source_file.filename

    single = len(files) == 1
    results: list[dict] = []
    for f in files:
        contents = await f.read()
        if not contents:
            results.append({
                "file_name": f.filename, "status": "error",
                "note": "That file is empty.",
            })
            continue
        try:
            gold = await create_gold_standard(
                file_name=f.filename or "gold.xlsx",
                contents=contents,
                name=name if single else None,
                template_id=(template_id or None) if single else None,
                source_file_name=src_name,
                source_contents=src_bytes or None,
                user_email=user.email,
            )
            results.append(_out(gold))
        except Exception as exc:  # noqa: BLE001 — one bad file must not sink the batch
            results.append({
                "file_name": f.filename, "status": "error", "note": str(exc),
            })

    return {
        "items": results,
        "uploaded": sum(1 for r in results if r.get("status") == "learned"),
        "unmatched": sum(1 for r in results if r.get("status") == "unmatched"),
        "failed": sum(1 for r in results if r.get("status") == "error"),
    }


class GoldPatch(BaseModel):
    name: str | None = None
    template_id: str | None = None


@router.patch("/{gold_id}")
async def patch_gold(gold_id: str, body: GoldPatch, _: User = Depends(get_current_user)):
    """Correct a gold standard's name, or the template it was matched to.

    Header detection is good but not infallible, and a wrong template isn't
    cosmetic — the rules were keyed to the wrong Oracle object and are being applied
    to the wrong conversions. So changing the template re-learns the file and re-keys
    its rules, and retires what it taught the old object if it was the last gold
    behind it. See ``repoint_gold``.
    """
    gold = await GoldStandard.get(PydanticObjectId(gold_id))
    if not gold:
        raise HTTPException(404, "Gold standard not found")

    result = await repoint_gold(gold, name=body.name, template_id=body.template_id)
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return {**_out(gold), "change": result}


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
