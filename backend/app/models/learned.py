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


class ArchivedMappingDecision(Document):
    """A superseded mapping decision, kept out of the way.

    THE POINT IS THAT NOTHING READS THIS.

    The store holds exactly one live decision per (client, source, field). When a
    newer statement arrives the older one is moved HERE and removed from
    ``LearnedMapping`` — so the guarantee is structural: the resolver queries
    LearnedMapping and there is only ever one row to find. Making this a flag on
    the row instead would have left the old statement sitting in the collection
    every reader already queries, which is the fallback the whole change exists
    to remove.

    Analyst, 05-Aug: "keep the older rules in archival currently, do not hard
    delete it but do not fall back to it, we will delete it after testing."

    So this is a holding pen with a deliberate end date, not a second store.
    Nothing in generation, mapping, learning or the UI may read it. When the
    analyst is satisfied, the collection is dropped and `_delete_other_decisions`
    goes back to deleting outright — the code for that is one branch, not a
    rewrite.

    The original ``_id`` is kept as ``original_id`` so a row can be traced back,
    and ``superseded_by`` records which decision replaced it.
    """
    kind: str
    category: Optional[str] = None
    original_value: Optional[str] = None
    resolved_value: Optional[str] = None
    target_object: Optional[str] = None
    target_field: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: Optional[dict] = None
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False
    project_id: Optional[PydanticObjectId] = None
    captured_from: Optional[str] = None
    captured_by: Optional[str] = None
    captured_at: Optional[datetime] = None
    effective_date: Optional[datetime] = None
    source_erp: Optional[str] = None
    sheets: list = Field(default_factory=list)
    exclude_sheets: list = Field(default_factory=list)
    is_deleted: bool = False

    #: The row this used to be, so it can be traced or restored by hand.
    original_id: Optional[PydanticObjectId] = None
    #: The decision that replaced it.
    superseded_by: Optional[PydanticObjectId] = None
    #: Why it left: "superseded" (a newer statement) or "collapsed" (the one-off
    #: migration bringing pre-existing history into line).
    reason: str = "superseded"
    archived_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "archived_mapping_decisions"
        indexes = ["target_field", "client_id", "archived_at"]
