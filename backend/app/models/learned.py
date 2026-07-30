"""Learned mappings registry."""
from datetime import datetime
from typing import Any, Optional

from beanie import Document, PydanticObjectId
from pydantic import Field


class LearnedMapping(Document):
    kind: str
    category: str
    original_value: str
    resolved_value: str
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[dict] = None
    # Tenant scope. A client-scoped learning applies only to conversions of that
    # client; a global learning (is_global=True, client_id=None) applies to all
    # clients. Legacy rows with neither set are treated as the default client by
    # the scoping query (back-compat) until the migration tags them.
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False
    project_id: Optional[PydanticObjectId] = None
    captured_from: Optional[str] = None
    captured_by: Optional[str] = None
    captured_at: datetime = Field(default_factory=datetime.utcnow)
    # WHEN THE INSTRUCTION WAS GIVEN, which is not when the row was written.
    #
    # Analyst, 30-Jul: "for conflicts always the latest one should be taken for
    # mapping". captured_at cannot answer that: every startup seed stamps itself
    # with utcnow, so a redeploy would make the 13-Jul strategy look newer than the
    # 30-Jul corrections and the ordering would flip on a restart. Seeded rows
    # therefore carry the _effective_date of the FILE they came from; rows captured
    # from an analyst's own action in the UI leave this null and fall back to
    # captured_at, which for them IS the moment the instruction was given.
    effective_date: Optional[datetime] = None
    confidence_boost: float = 0.26
    records_auto_fixed: int = 0
    times_reused: int = 0
    originated_in_project_id: Optional[PydanticObjectId] = None
    source_erp: Optional[str] = None
    # Tombstone (QA issue #5). Deleting a learning used to hard-delete the row,
    # but three paths recreate it — startup seeds (find-or-insert), auto-capture
    # after Generate Output, and approve/override in Mapping Review — so deleted
    # items reappeared. A delete now marks the row retired instead: it stops
    # applying and stops being listed, and `_upsert`/the seeders refuse to
    # resurrect it unless the user explicitly restores it.
    is_deleted: bool = False
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[str] = None

    # ── Sheet scope ──────────────────────────────────────────────────────
    # A learning is keyed by target field NAME, and Oracle repeats the same name
    # across many interface sheets — Customer has 19. So approving one mapping
    # applied it to EVERY sheet carrying that name, including ones where the
    # field must stay blank (bank, pay). These two lists make that controllable:
    # `sheets` restricts a learning to a named set, `exclude_sheets` removes it
    # from specific ones. Both empty = every sheet, which is the previous
    # behaviour and what every existing row means, so nothing needs migrating.
    sheets: list[str] = Field(default_factory=list)
    exclude_sheets: list[str] = Field(default_factory=list)

    class Settings:
        name = "learned_mappings"

    # ── Tombstone-aware queries ──────────────────────────────────────────
    # Retired learnings must disappear from EVERY read path — the Learning
    # Center lists, the apply/steering passes, defaults, mapping candidates.
    # There are ~40 query sites across 18 modules, so filtering here (rather
    # than at each call site) is what makes the guarantee hold, including for
    # code added later. Pass ``include_deleted=True`` to see retired rows
    # (used by the delete/restore endpoints).
    @classmethod
    def find(cls, *args, include_deleted: bool = False, **kwargs):
        if not include_deleted:
            args = (*args, {"is_deleted": {"$ne": True}})
        return super().find(*args, **kwargs)

    @classmethod
    def find_one(cls, *args, include_deleted: bool = False, **kwargs):
        if not include_deleted:
            args = (*args, {"is_deleted": {"$ne": True}})
        return super().find_one(*args, **kwargs)

    @classmethod
    def find_all(cls, *args, include_deleted: bool = False, **kwargs):
        if include_deleted:
            return super().find_all(*args, **kwargs)
        return super().find({"is_deleted": {"$ne": True}}, *args, **kwargs)
