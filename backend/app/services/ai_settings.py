"""Runtime-selectable Anthropic model — lets the user trade cost vs capability.

The chosen model is persisted in a singleton AppSetting document AND mirrored
into settings.ANTHROPIC_MODEL in-memory, so every AI service (which already
reads settings.ANTHROPIC_MODEL) picks it up immediately with no per-call DB read.
On startup we reload the persisted value back into settings.
"""
from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

_KEY = "anthropic_model"
_DEFAULT = "claude-sonnet-4-6"

# Ordered lowest → highest cost/capability so the UI can present a clear scale.
AI_MODELS = [
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5",
     "tier": "Fastest · lowest cost"},
    {"id": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6",
     "tier": "Balanced (recommended)"},
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8",
     "tier": "Most capable · highest cost"},
]
_ALLOWED = {m["id"] for m in AI_MODELS}


def get_active_model() -> str:
    """The model the AI services should use right now (in-memory source of truth)."""
    return (settings.ANTHROPIC_MODEL or _DEFAULT)


async def load_persisted_model() -> None:
    """On startup, load the persisted model choice into settings.ANTHROPIC_MODEL."""
    try:
        from app.models.app_setting import AppSetting
        doc = await AppSetting.find_one(AppSetting.key == _KEY)
        if doc and doc.value and doc.value in _ALLOWED:
            settings.ANTHROPIC_MODEL = doc.value
            log.info("AI model loaded from settings: %s", doc.value)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not load persisted AI model (%s)", e)


async def set_active_model(model: str) -> str:
    """Validate, persist, and apply the model choice."""
    if model not in _ALLOWED:
        raise ValueError(f"Unsupported model '{model}'.")
    from datetime import datetime
    from app.models.app_setting import AppSetting
    doc = await AppSetting.find_one(AppSetting.key == _KEY)
    if doc:
        await doc.set({"value": model, "updated_at": datetime.utcnow()})
    else:
        await AppSetting(key=_KEY, value=model).insert()
    try:
        settings.ANTHROPIC_MODEL = model
    except Exception:  # noqa: BLE001
        pass
    return model
