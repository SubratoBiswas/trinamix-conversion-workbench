"""Client (tenant) resolution + scoping helpers.

Central place for the multi-tenant rules so every apply/capture path stays
consistent:

* ``ensure_default_client`` / ``default_client_id`` — the bootstrap "NextPower"
  client that legacy (pre-tenant) data and un-cliented projects attach to.
* ``client_id_for_conversion`` — a conversion's tenant, via its project.
* ``scope_filter`` — the Mongo fragment that selects rows visible to a client:
  the client's own rows PLUS anything marked global. Used on every learning /
  gold / reference-standard read so a future client never inherits another
  client's source-system knowledge, while the shared Oracle-standard rows still
  apply everywhere.
"""
from __future__ import annotations

from typing import Optional

from beanie import PydanticObjectId

from app.models.client import Client

_DEFAULT_NAME = "NextPower"
_default_id: Optional[PydanticObjectId] = None  # process cache


async def ensure_default_client() -> Client:
    """Get or create the default (NextPower) client. Idempotent."""
    global _default_id
    existing = await Client.find_one(Client.is_default == True)  # noqa: E712
    if existing:
        _default_id = existing.id
        return existing
    # No default yet — adopt an existing same-named client or create one.
    named = await Client.find_one(Client.name == _DEFAULT_NAME)
    if named:
        named.is_default = True
        await named.save()
        _default_id = named.id
        return named
    c = Client(name=_DEFAULT_NAME, code="NEXTPOWER", is_default=True,
               description="Bootstrap tenant — all pre-multi-tenant data belongs here.")
    await c.insert()
    _default_id = c.id
    return c


async def default_client_id() -> Optional[PydanticObjectId]:
    global _default_id
    if _default_id is not None:
        return _default_id
    try:
        c = await Client.find_one(Client.is_default == True)  # noqa: E712
    except Exception:  # noqa: BLE001 — never let tenant lookup break mapping/generate
        return None
    _default_id = c.id if c else None
    return _default_id


async def client_id_for_conversion(conversion) -> Optional[PydanticObjectId]:
    """Resolve the tenant for a conversion via its project; fall back to default."""
    try:
        from app.models.project import Project
        pid = getattr(conversion, "project_id", None)
        if pid:
            proj = await Project.get(pid)
            if proj and getattr(proj, "client_id", None):
                return proj.client_id
    except Exception:  # noqa: BLE001 — never block on resolution
        pass
    return await default_client_id()


async def explicit_client_id_for_conversion(conversion) -> Optional[PydanticObjectId]:
    """The client this conversion is EXPLICITLY tagged with, or None if nobody said.

    ``client_id_for_conversion`` falls back to the default client, which is right for
    a READ - an untagged conversion should still see the bootstrap tenant's library.
    It is wrong for a SCOPE COMPARISON, and quietly so: the fallback turns "nobody
    tagged this project" into a real, specific client id before the comparison ever
    sees it, so an untagged project reads as a DIFFERENT TENANT from a tagged one and
    a correction made in the tagged project is skipped as a cross-tenant leak.

    That is the reported symptom - "the mapping I changed in one project did not
    refresh the mapping of another existing project when client and source is same" -
    and the check it defeated was written to allow exactly this case. Its comment says
    "a row nobody tagged is not another tenant's, so it is in scope", and its
    ``cconv is not None`` guard could never be false, because the fallback had already
    substituted an id. A guard that reads as protective and cannot fire.

    So: this function answers the question the scope check is actually asking, and
    returns None when the answer is genuinely unknown.
    """
    try:
        from app.models.project import Project
        pid = getattr(conversion, "project_id", None)
        if pid:
            proj = await Project.get(pid)
            if proj and getattr(proj, "client_id", None):
                return proj.client_id
    except Exception:  # noqa: BLE001 — never block on resolution
        pass
    return None


def scope_filter(client_id: Optional[PydanticObjectId]) -> dict:
    """Synchronous filter fragment: rows for this client OR global rows. When the
    client is unknown (None), return an empty filter (behave like the old
    global-only world rather than hiding everything)."""
    if client_id is None:
        return {}
    return {"$or": [{"is_global": True}, {"client_id": client_id}]}


async def run_client_scope_migration() -> dict:
    """One-time (idempotent) tenant tagging of pre-multi-tenant data:

    * bundled FBDI/HDL templates + the public-schema catalog learnings -> GLOBAL;
    * every other untagged learning, gold record and project -> the NextPower
      (default) client.

    Only touches rows that are still untagged (is_global unset / client_id null),
    so it is a no-op on subsequent boots and never re-globalises a genuinely
    client-scoped template a user later uploads.
    """
    from datetime import datetime
    from app.models.app_setting import AppSetting
    from app.models.fbdi import FBDITemplate, GoldStandard
    from app.models.learned import LearnedMapping
    from app.models.project import Project

    c = await ensure_default_client()
    nid = c.id
    res: dict = {"default_client": str(nid)}

    # Run the blanket tagging exactly ONCE. After the first pass, new untagged rows
    # created by a specific client's capture must NOT be swept into NextPower on a
    # later reboot (that would leak one client's rules into another), and a bespoke
    # client template must not be re-globalised. Seeders/captures tag their own rows
    # from here on.
    done = await AppSetting.find_one(AppSetting.key == "client_scope_migration_done")
    if done:
        res["status"] = "already migrated"
        return res

    r = await FBDITemplate.get_motor_collection().update_many(
        {"is_global": {"$ne": True}, "client_id": None}, {"$set": {"is_global": True}})
    res["templates_global"] = r.modified_count
    # Public-schema catalog rows are global; all other legacy learnings are NextPower's.
    r = await LearnedMapping.get_motor_collection().update_many(
        {"captured_from": "metadata catalog", "is_global": {"$ne": True}},
        {"$set": {"is_global": True}})
    res["catalog_global"] = r.modified_count
    r = await LearnedMapping.get_motor_collection().update_many(
        {"is_global": {"$ne": True}, "client_id": None}, {"$set": {"client_id": nid}})
    res["learnings_nextpower"] = r.modified_count
    r = await GoldStandard.get_motor_collection().update_many(
        {"client_id": None}, {"$set": {"client_id": nid}})
    res["gold_nextpower"] = r.modified_count
    r = await Project.get_motor_collection().update_many(
        {"client_id": None}, {"$set": {"client_id": nid}})
    res["projects_nextpower"] = r.modified_count

    await AppSetting(key="client_scope_migration_done",
                     value=datetime.utcnow().isoformat()).insert()
    res["status"] = "migrated"
    return res


async def scope_query(client_id: Optional[PydanticObjectId]) -> dict:
    """Preferred read filter. Like ``scope_filter`` but, for the DEFAULT client,
    also includes legacy/untagged rows (client_id unset and not global) so any
    capture path not yet tenant-tagged still resolves for the bootstrap tenant —
    no regression. A non-default client only ever sees its own rows + global."""
    if client_id is None:
        return {}
    ors: list[dict] = [{"is_global": True}, {"client_id": client_id}]
    default = await default_client_id()
    if default is not None and client_id == default:
        ors.append({"client_id": None, "is_global": {"$ne": True}})
    return {"$or": ors}
