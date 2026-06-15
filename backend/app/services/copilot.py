"""AI Copilot — MongoDB/Beanie version."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from app.config import settings
from app.models.project import Project
from app.models.v10 import Issue, Risk

log = logging.getLogger("trinamix.copilot")


class CopilotUnavailable(Exception):
    pass


class CopilotError(Exception):
    pass


@dataclass
class CopilotMessage:
    role: str
    content: str


@dataclass
class CopilotResponse:
    answer: str
    citations: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = """\
You are the Trinamix Conversion Workbench Copilot. You answer questions \
about a specific Oracle Fusion implementation engagement. Ground every \
answer in the project context provided. Be concise (3-6 sentences max). \
You cannot mutate state or run jobs.
"""


async def _build_context(project: Project) -> str:
    from beanie import PydanticObjectId
    oid = project.id
    issues = await Issue.find(
        Issue.project_id == oid,
        {"status": {"$in": ["open", "in_progress", "blocked"]}},
    ).sort("-created_at").limit(10).to_list()
    risks = await Risk.find(
        Risk.project_id == oid,
        {"status": {"$ne": "closed"}},
    ).sort("-updated_at").limit(5).to_list()

    ctx = {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "client": project.client,
            "phase": getattr(project, "phase", None),
            "go_live_date": str(project.go_live_date) if project.go_live_date else None,
            "current_environment": getattr(project, "current_environment", "DEV"),
        },
        "open_issues": [
            {"title": i.title, "severity": i.severity, "status": i.status}
            for i in issues
        ],
        "top_risks": [
            {"title": r.title, "likelihood": r.likelihood, "impact": r.impact}
            for r in risks
        ],
    }
    return json.dumps(ctx, default=str, separators=(",", ":"))


async def chat(*, project: Project, messages: list[CopilotMessage]) -> CopilotResponse:
    if not settings.ANTHROPIC_API_KEY:
        raise CopilotUnavailable("ANTHROPIC_API_KEY not configured.")
    if not messages or messages[-1].role != "user":
        raise CopilotError("Last message must be from user.")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise CopilotUnavailable("anthropic SDK not installed.") from e

    context_json = await _build_context(project)
    client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = client.messages.create(
            model=getattr(settings, "ANTHROPIC_MODEL", None) or "claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=f"{_SYSTEM_PROMPT}\n\nPROJECT_CONTEXT:\n{context_json}",
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
    except Exception as exc:
        log.warning("copilot API call failed: %s", exc)
        raise CopilotError(f"Anthropic API call failed: {exc}") from exc

    text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    answer = "\n".join(text_blocks).strip() or "(no answer)"
    return CopilotResponse(answer=answer)
