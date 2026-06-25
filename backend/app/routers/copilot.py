"""AI Copilot router — MongoDB/Beanie version."""
from __future__ import annotations
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
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


class SuggestDefaultRequest(BaseModel):
    column_name: str
    samples: List[Any] = []
    null_percent: float = 0.0
    target_field: Optional[str] = None
    target_data_type: Optional[str] = None


@router.post("/suggest-default")
async def suggest_default(
    payload: SuggestDefaultRequest,
    _: User = Depends(get_current_user),
):
    """Ask Claude to suggest a sensible default value for a column with missing data."""
    if not settings.ANTHROPIC_API_KEY:
        return {"suggestion": "", "available": False, "reason": "ANTHROPIC_API_KEY not configured"}

    try:
        from anthropic import Anthropic
    except ImportError:
        return {"suggestion": "", "available": False, "reason": "anthropic SDK not installed"}

    samples_str = ", ".join(str(s) for s in payload.samples[:6] if s is not None and str(s).strip())
    target_info = f"Maps to Oracle Fusion FBDI field: {payload.target_field}" if payload.target_field else ""
    dtype_info = f"Expected data type: {payload.target_data_type}" if payload.target_data_type else ""

    prompt = f"""Oracle Fusion Cloud EBS-to-Fusion migration context.
Source column: {payload.column_name}
Sample existing values: {samples_str or "(all empty/null)"}
{target_info}
{dtype_info}
{payload.null_percent:.0f}% of rows are missing this value.

Suggest ONE concise default value appropriate for an Oracle Fusion data load.
Rules:
- Return ONLY the value itself, no explanation or punctuation
- UOM columns: standard Oracle codes (EA, LB, KG, FT, HR)
- Status/flag columns: Active or Inactive
- Currency columns: USD, GBP, EUR, CAD
- Country columns: US, GB, CA, AU
- Date columns: use SYSDATE or a sensible fixed date YYYY-MM-DD
- Numeric/amount columns: 0
- If genuinely ambiguous, return empty string
Return just the value, nothing else."""

    try:
        client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=30,
            messages=[{"role": "user", "content": prompt}],
        )
        suggestion = resp.content[0].text.strip() if resp.content else ""
        # Strip surrounding quotes if AI added them
        suggestion = suggestion.strip("\"'")
        return {"suggestion": suggestion, "available": True}
    except Exception as exc:
        return {"suggestion": "", "available": True, "reason": str(exc)}
