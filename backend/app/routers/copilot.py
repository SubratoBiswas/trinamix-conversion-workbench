"""AI Copilot router — MongoDB/Beanie version."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.models.project import Project
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.copilot import (
    CopilotError, CopilotMessage, CopilotResponse, CopilotUnavailable, chat,
)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


class CopilotMessageIn(BaseModel):
    role: str
    content: str


class CopilotAskRequest(BaseModel):
    project_id: str
    messages: list[CopilotMessageIn]


class CopilotAnswer(BaseModel):
    answer: str
    citations: list[str] = []


@router.post("/ask", response_model=CopilotAnswer,
             responses={503: {"description": "Copilot unavailable"}})
async def ask(
    payload: CopilotAskRequest,
    _: User = Depends(get_current_user),
):
    project = await Project.get(payload.project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        resp: CopilotResponse = await chat(
            project=project,
            messages=[CopilotMessage(role=m.role, content=m.content) for m in payload.messages],
        )
    except CopilotUnavailable as e:
        raise HTTPException(503, str(e))
    except CopilotError as e:
        raise HTTPException(502, str(e))
    return CopilotAnswer(answer=resp.answer, citations=resp.citations)
