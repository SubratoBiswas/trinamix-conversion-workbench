"""Global app settings — currently the Anthropic model selector."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.ai_settings import AI_MODELS, get_active_model, set_active_model

router = APIRouter(prefix="/api/settings", tags=["settings"])


class AiModelIn(BaseModel):
    model: str


@router.get("/ai-model")
async def get_ai_model(_: User = Depends(get_current_user)):
    """The active Anthropic model + the selectable options (lowest→highest cost)."""
    return {"current": get_active_model(), "options": AI_MODELS}


@router.put("/ai-model")
async def put_ai_model(body: AiModelIn, _: User = Depends(get_current_user)):
    try:
        current = await set_active_model(body.model)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"current": current, "options": AI_MODELS}


@router.post("/collapse-decisions")
async def collapse_decisions(dry_run: bool = True,
                             _: User = Depends(get_current_user)):
    """Bring pre-existing store rows into line with the one-row-per-key rule.

    WHY THIS IS AN ENDPOINT AND NOT A SCRIPT.

    The rule governs writes from now on; anything stored under the old shape keeps
    up to four live rows per field until somebody edits it, and a key nobody
    touches again keeps them forever. So it has to be run once — and the only
    machine that can run it is the one that can reach Mongo. The deploy box has no
    Python environment and no backend dependencies (see launch_git.bat), so a
    script there is not a way anyone can actually use.

    This router is mounted under the ADMIN section, so it is administrators only,
    which is the right audience for a migration.

    ``dry_run`` DEFAULTS TO TRUE. Running it by accident must report and change
    nothing; moving rows takes an explicit ``?dry_run=false``. It is idempotent
    either way, and superseded rows are archived rather than deleted — see
    ArchivedMappingDecision.

    Returns ``{keys, keys_with_duplicates, rows_removed, dry_run}``.
    """
    from app.services.mapping_store import collapse_existing_decisions

    return await collapse_existing_decisions(dry_run=dry_run)


@router.post("/purge-gold-learnings")
async def purge_gold_learnings_endpoint(dry_run: bool = True,
                                        _: User = Depends(get_current_user)):
    """Remove every learning captured from a gold output — across all objects.

    Analyst, 05-Aug: "How to remove all the gold output learning till today." Gold
    uploads seed the store with defaults, suppressions and mappings that auto-apply
    to every conversion of the object; this wipes all of them in one action so the
    store holds only what people have decided since.

    Admin-only (this router is mounted under ADMIN). ``dry_run`` DEFAULTS TO TRUE —
    the bare call reports what WOULD go and changes nothing; ``?dry_run=false``
    performs it. Rows are ARCHIVED before removal (reason=gold-purge), so it is
    recoverable, and it is idempotent. Regenerate affected conversions afterwards
    to drop the values from the outputs on disk.

    Returns ``{dry_run, gold_learnings_found, by_kind, removed, note}``.
    """
    from app.services.gold_library_service import purge_gold_learnings

    return await purge_gold_learnings(dry_run=dry_run)
