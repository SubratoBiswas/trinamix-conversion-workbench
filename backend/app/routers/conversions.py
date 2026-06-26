"""Conversions router."""
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDITemplate
from app.models.project import Project
from app.models.user import User
from app.schemas.conversion import ConversionCreate, ConversionOut, ConversionUpdate
from app.services.auth_service import get_current_user


async def _auto_map(conversion_id) -> None:
    """Run AI mapping suggestions in the background if none exist yet.

    Before mapping, ensures the linked FBDI template has field records —
    if the Excel upload parser returned 0 fields, auto-seeds from the
    Oracle Fusion standard schema dictionary so mapping always produces results.
    """
    try:
        from app.models.fbdi import FBDIField
        from app.models.mapping import MappingSuggestion
        from app.services.mapping_service import run_mapping_suggestions

        conv = await Conversion.get(conversion_id)
        if not conv or not conv.template_id:
            return
        # Require either a dataset (dataset mode) or an EBS table hint (ebs mode)
        if conv.source_type != "ebs" and not conv.dataset_id:
            return

        # Auto-seed standard fields if the template has none (handles templates
        # whose Excel couldn't be parsed on upload)
        tpl = await FBDITemplate.get(conv.template_id)
        if tpl:
            field_count = await FBDIField.find(
                FBDIField.template_id == tpl.id
            ).count()
            if field_count == 0:
                from app.routers.fbdi_seed import auto_seed_if_empty
                seeded = await auto_seed_if_empty(tpl)
                if seeded:
                    import logging
                    logging.getLogger(__name__).info(
                        f"_auto_map: seeded {seeded} standard fields for '{tpl.name}' "
                        f"before running mapping on conversion {conversion_id}"
                    )

        existing = await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion_id
        ).count()
        if existing == 0:
            await run_mapping_suggestions(conv)
    except Exception:
        pass  # Background task -- never crash the request

router = APIRouter(prefix="/api/conversions", tags=["conversions"])


async def _hydrate(c: Conversion) -> ConversionOut:
    """Hydrate a single conversion (used for create/update responses)."""
    data = {**c.model_dump(), "id": str(c.id), "project_id": str(c.project_id)}
    if c.dataset_id:
        data["dataset_id"] = str(c.dataset_id)
        ds = await Dataset.get(c.dataset_id)
        data["dataset_name"] = ds.name if ds else None
    if c.template_id:
        data["template_id"] = str(c.template_id)
        tmpl = await FBDITemplate.get(c.template_id)
        data["template_name"] = tmpl.name if tmpl else None
    proj = await Project.get(c.project_id)
    data["project_name"] = proj.name if proj else None
    return ConversionOut(**data)


async def _hydrate_bulk(convs: list[Conversion]) -> list[ConversionOut]:
    """Hydrate many conversions with bulk lookups — avoids N+1 queries."""
    if not convs:
        return []

    # Collect unique IDs
    project_ids = list({c.project_id for c in convs})
    dataset_ids = list({c.dataset_id for c in convs if c.dataset_id})
    template_ids = list({c.template_id for c in convs if c.template_id})

    # Bulk fetch in 3 queries instead of 3*N
    projects_list = await Project.find({"_id": {"$in": project_ids}}).to_list()
    datasets_list = await Dataset.find({"_id": {"$in": dataset_ids}}).to_list() if dataset_ids else []
    templates_list = await FBDITemplate.find({"_id": {"$in": template_ids}}).to_list() if template_ids else []

    proj_map = {p.id: p for p in projects_list}
    ds_map = {d.id: d for d in datasets_list}
    tpl_map = {t.id: t for t in templates_list}

    results = []
    for c in convs:
        data = {**c.model_dump(), "id": str(c.id), "project_id": str(c.project_id)}
        if c.dataset_id:
            data["dataset_id"] = str(c.dataset_id)
            ds = ds_map.get(c.dataset_id)
            data["dataset_name"] = ds.name if ds else None
        if c.template_id:
            data["template_id"] = str(c.template_id)
            tmpl = tpl_map.get(c.template_id)
            data["template_name"] = tmpl.name if tmpl else None
        proj = proj_map.get(c.project_id)
        data["project_name"] = proj.name if proj else None
        results.append(ConversionOut(**data))
    return results


@router.get("", response_model=list[ConversionOut])
async def list_conversions(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
):
    query = {}
    if project_id:
        query["project_id"] = PydanticObjectId(project_id)
    if status:
        query["status"] = status
    convs = await Conversion.find(query).sort("planned_load_order").to_list()
    return await _hydrate_bulk(convs)


@router.get("/{conversion_id}", response_model=ConversionOut)
async def get_conversion(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    return await _hydrate(c)


@router.post("", response_model=ConversionOut)
async def create_conversion(
    payload: ConversionCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
):
    proj = await Project.get(PydanticObjectId(payload.project_id))
    if not proj:
        raise HTTPException(400, f"Project {payload.project_id} does not exist")
    data = payload.model_dump(exclude_unset=True)
    data["project_id"] = PydanticObjectId(payload.project_id)
    if data.get("dataset_id"):
        data["dataset_id"] = PydanticObjectId(data["dataset_id"])
    if data.get("template_id"):
        data["template_id"] = PydanticObjectId(data["template_id"])
    data["created_by"] = user.email
    data["created_at"] = datetime.utcnow()
    data["updated_at"] = datetime.utcnow()
    if not data.get("status"):
        data["status"] = "draft" if data.get("dataset_id") and data.get("template_id") else "planning"
    c = Conversion(**data)
    await c.insert()
    if c.dataset_id and c.template_id:
        background_tasks.add_task(_auto_map, c.id)
    return await _hydrate(c)


@router.patch("/{conversion_id}", response_model=ConversionOut)
async def update_conversion(
    conversion_id: str,
    payload: ConversionUpdate,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    update_data = payload.model_dump(exclude_unset=True)
    if "dataset_id" in update_data and update_data["dataset_id"]:
        update_data["dataset_id"] = PydanticObjectId(update_data["dataset_id"])
    if "template_id" in update_data and update_data["template_id"]:
        update_data["template_id"] = PydanticObjectId(update_data["template_id"])
    update_data["updated_at"] = datetime.utcnow()
    await c.set(update_data)
    await c.sync()
    if c.dataset_id and c.template_id:
        background_tasks.add_task(_auto_map, c.id)
    return await _hydrate(c)


@router.delete("/{conversion_id}")
async def delete_conversion(conversion_id: str, _: User = Depends(get_current_user)):
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    # Cascade so nothing is orphaned (mappings/rules/outputs/load runs).
    from app.models.mapping import MappingSuggestion
    from app.models.transformation import TransformationRule, Crosswalk
    from app.models.output import ConvertedOutput
    from app.models.load import LoadRun, LoadError
    await MappingSuggestion.find(MappingSuggestion.conversion_id == c.id).delete()
    await TransformationRule.find(TransformationRule.conversion_id == c.id).delete()
    await Crosswalk.find(Crosswalk.conversion_id == c.id).delete()
    await ConvertedOutput.find(ConvertedOutput.conversion_id == c.id).delete()
    runs = await LoadRun.find(LoadRun.conversion_id == c.id).to_list()
    if runs:
        await LoadError.find({"load_run_id": {"$in": [r.id for r in runs]}}).delete()
    await LoadRun.find(LoadRun.conversion_id == c.id).delete()
    await c.delete()
    return {"deleted": conversion_id}


@router.post("/project/{project_id}/use-ebs-source", status_code=200)
async def switch_project_to_ebs_source(
    project_id: str,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
):
    """Switch all conversions in a project to live Oracle EBS as the data source.

    Clears any uploaded dataset link and sets source_type='ebs'. The EBS table
    hint is derived from the fusion_modules catalog using the conversion's
    target_object. Mapping suggestions are re-triggered in the background.
    """
    from app.fusion_modules import MODULES
    import re

    def _first_table(extract_hint: str) -> str:
        """Parse first table name from 'Extract from TABLE1, TABLE2 ...'"""
        m = re.search(r"from\s+([A-Z_][A-Z0-9_#$]+)", extract_hint or "", re.IGNORECASE)
        return m.group(1).upper() if m else ""

    # Build target_object -> EBS table map from the catalog
    obj_to_table: dict[str, str] = {}
    for mod in MODULES:
        for obj in mod.objects:
            hint = (obj.source_extracts or {}).get("oracle_ebs", "")
            table = _first_table(hint)
            if table:
                obj_to_table[obj.target_object.lower()] = table

    convs = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).to_list()

    if not convs:
        raise HTTPException(404, f"No conversions found for project {project_id}")

    updated = 0
    for conv in convs:
        target_key = (conv.target_object or "").lower()
        table_hint = obj_to_table.get(target_key, "")
        await conv.set({
            "source_type": "ebs",
            "dataset_id": None,
            "ebs_table_hint": table_hint,
            "updated_at": datetime.utcnow(),
        })
        if conv.template_id:
            background_tasks.add_task(_auto_map, conv.id)
        updated += 1

    return {
        "updated": updated,
        "message": f"Switched {updated} conversions to Oracle EBS live source",
    }
