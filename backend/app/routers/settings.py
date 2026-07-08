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
