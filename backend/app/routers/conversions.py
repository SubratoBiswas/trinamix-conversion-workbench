"""Conversions router."""
import logging
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

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


@router.get("/object-types")
async def list_object_types(_: User = Depends(get_current_user)):
    """Catalog of conversion object types and the FBDI template set each needs."""
    from app.services.object_fanout_service import object_types
    return {"object_types": object_types()}


class GenerateSetRequest(BaseModel):
    project_id: str
    dataset_id: str
    object_type: str


@router.post("/generate-set")
async def generate_object_set(
    payload: GenerateSetRequest,
    background_tasks: BackgroundTasks,
    _: User = Depends(get_current_user),
):
    """One dataset -> all FBDI templates for the object type (Req 1).

    Creates one auto-mapped conversion per resolved template, sets the load
    order, chains the load-sequence dependencies, and reports any template the
    object needs that isn't seeded yet.
    """
    from app.services.object_fanout_service import generate_object_template_set
    proj = await Project.get(PydanticObjectId(payload.project_id))
    if not proj:
        raise HTTPException(400, f"Project {payload.project_id} does not exist")
    result = await generate_object_template_set(
        payload.project_id, payload.dataset_id, payload.object_type
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    for item in result.get("created", []):
        cid = item.get("conversion_id")
        if cid:
            background_tasks.add_task(_auto_map, PydanticObjectId(cid))
    return result


async def _apply_reference_standard(conv: Conversion) -> dict:
    """Force-apply the object's stored gold reference standard to one conversion,
    overriding AI-approved mappings (human 'overridden' mappings are preserved)."""
    from app.models.mapping import MappingSuggestion
    from app.services.learning_service import apply_learned_to_conversion
    maps = await MappingSuggestion.find(MappingSuggestion.conversion_id == conv.id).to_list()
    applied = await apply_learned_to_conversion(conv, maps, force=True)
    return {"conversion_id": str(conv.id),
            "target_object": conv.target_object, "applied": applied}


@router.post("/{conversion_id}/apply-reference-standard")
async def apply_reference_standard(conversion_id: str, _: User = Depends(get_current_user)):
    """Apply the stored gold reference standard to this conversion now (no
    re-upload, no regenerate) so its mappings reflect the learned standard."""
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    return await _apply_reference_standard(conv)


@router.post("/project/{project_id}/apply-reference-standards")
async def apply_reference_standards_project(project_id: str, _: User = Depends(get_current_user)):
    """Apply stored gold reference standards to every conversion in the project."""
    convs = await Conversion.find(
        Conversion.project_id == PydanticObjectId(project_id)
    ).to_list()
    objects = []
    total = 0
    for c in convs:
        r = await _apply_reference_standard(c)
        total += r["applied"]
        objects.append(r)
    return {"applied": total, "objects": objects}


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


@router.get("/{conversion_id}/effective-defaults")
async def get_effective_defaults(
    conversion_id: str,
    use_ai: bool = Query(True),
    _: User = Depends(get_current_user),
):
    """Values that Generate Output writes for unmapped target fields (control
    constants, sequence keys, learned + AI-inferred defaults). The mapping-review
    UI uses this to render 'defaulted -> value' instead of a red required gap."""
    c = await Conversion.get(PydanticObjectId(conversion_id))
    if not c:
        raise HTTPException(404, "Conversion not found")
    from app.services.defaults_service import compute_effective_defaults
    return await compute_effective_defaults(c, use_ai=use_ai)


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


@router.post("/{conversion_id}/learn-from-example")
async def learn_from_example(
    conversion_id: str,
    file: UploadFile | None = File(None),
    prompt: str | None = Form(None),
    _: User = Depends(get_current_user),
):
    """Teach this conversion from a populated example output and/or a plain-text
    steering prompt. Infers source->target mappings + constant defaults from the
    example (value-set matching) and applies any prompt directives, writing them
    as approved mappings so Generate Output reproduces the example."""
    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    if not conv.template_id:
        raise HTTPException(422, "Conversion has no target template")

    result: dict = {}
    if file is not None:
        import os
        import tempfile
        from app.services.example_learning_service import learn_conversion_from_example
        contents = await file.read()
        suffix = os.path.splitext(file.filename or "")[1] or ".xlsx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(contents)
            tmp = tf.name
        try:
            result["learned"] = await learn_conversion_from_example(conv, tmp)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

        # Also file it in the shared Gold Standards library. Historically this
        # endpoint learned from the upload and then threw the bytes away, so the
        # gold file itself was unrecoverable — you could see the rules it produced
        # but never the file, and it couldn't be re-learned after a rule-engine
        # change. Keep a durable copy so a gold uploaded on a conversion behaves
        # exactly like one uploaded to the library.
        try:
            from app.services.gold_library_service import register_gold_from_conversion
            reg = await register_gold_from_conversion(
                conv, file_name=file.filename or "gold.xlsx", contents=contents,
                learned=result.get("learned") or {},
            )
            if reg:
                result["gold_standard_id"] = reg
        except Exception:  # noqa: BLE001 — never fail the learn on a bookkeeping error
            logging.getLogger(__name__).exception("could not add gold to library")

    if prompt and prompt.strip():
        from app.services.steering_service import apply_steer_prompt
        result["steer"] = await apply_steer_prompt(conv, prompt)

    if not result:
        raise HTTPException(400, "Provide an example file and/or a prompt")
    return result


class ResetDefaultsIn(BaseModel):
    # None → every gold-derived default for the object. Otherwise just these fields.
    fields: Optional[list[str]] = None
    include_ai_defaults: bool = False   # also drop AI-inferred defaults
    forget_global: bool = True          # remove the object-level rule so it can't reapply
    rerun_ai: bool = False              # re-map the cleared fields afterward


@router.post("/{conversion_id}/reset-defaults")
async def reset_defaults(
    conversion_id: str,
    body: ResetDefaultsIn,
    _: User = Depends(get_current_user),
):
    """Remove learned/gold-derived constant defaults from a conversion.

    A constant like Country → AE gets captured when a gold file happens to hold one
    value in every row — great for that client, wrong for the next. Clearing it on
    the conversion alone isn't enough: the rule is stored globally and re-applied on
    every regenerate. So (with forget_global, the default) this also deletes the
    object-level rule, and optionally re-runs AI to re-fill the freed fields.

    Control defaults (Import Action = CREATE, batch ids, running numbers) are NOT
    touched — they're structural, not gold-derived.
    """
    try:
        return await _reset_defaults_impl(conversion_id, body)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — surface the real reason, not an opaque 500
        logging.getLogger(__name__).exception("reset-defaults failed")
        raise HTTPException(400, f"Reset failed: {exc}")


async def _reset_defaults_impl(conversion_id: str, body: "ResetDefaultsIn") -> dict:
    from app.models.fbdi import FBDIField
    from app.models.learned import LearnedMapping
    from app.models.mapping import MappingSuggestion
    from app.services.learning_service import _business_object_for
    from app.services.mapping_service import run_mapping_suggestions

    conv = await Conversion.get(PydanticObjectId(conversion_id))
    if not conv:
        raise HTTPException(404, "Conversion not found")
    # Rules are keyed by the TEMPLATE's business object (falling back to the
    # conversion's target_object) — the same derivation the learning engine uses
    # when it stores them. Keying off conv.target_object alone missed conversions
    # whose object lives only on the template, which is what made this 422.
    obj = (await _business_object_for(conv)) or ""
    if not obj:
        raise HTTPException(422, "Conversion has no target object")

    want_fields = {f.strip().lower() for f in (body.fields or []) if f.strip()}

    # 1. Which stored example_default rules to forget. Gold-derived by default;
    #    AI-inferred too if asked. Never touches column mappings or suppressions.
    sources_ok = {"gold example"}
    # When the user targets specific fields, forget whatever default feeds them —
    # including an AI-inferred one — so a single removal actually sticks instead of
    # being re-applied on the next effective-defaults recompute.
    if body.include_ai_defaults or want_fields:
        sources_ok.add("ai-inference")
    rules = await LearnedMapping.find(
        LearnedMapping.kind == "example_default",
        LearnedMapping.target_object == obj,
    ).to_list()
    rules = [
        r for r in rules
        if ((r.captured_from or "") in sources_ok or "gold" in (r.captured_from or "").lower())
        and (not want_fields or (r.target_field or "").strip().lower() in want_fields)
    ]
    forget_field_keys = {(r.target_field or "").strip().lower() for r in rules if r.target_field}

    rules_forgotten = 0
    if body.forget_global:
        for r in rules:
            await r.delete()
            rules_forgotten += 1

    # 2. Clear the default off this conversion's mappings for those fields, so the
    #    field goes empty and becomes eligible for AI/deterministic re-mapping.
    fields = await FBDIField.find(FBDIField.template_id == conv.template_id).to_list()
    id_to_key = {f.id: (f.field_name or "").strip().lower() for f in fields}
    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conv.id
    ).to_list()

    cleared = 0
    for m in maps:
        key = id_to_key.get(m.target_field_id)
        if key is None:
            continue
        # Only fields we're resetting, and only where the value is a default (no real
        # source column). A field mapped to a source column is left alone.
        target = (not forget_field_keys and not want_fields) or key in forget_field_keys or key in want_fields
        if not target:
            continue
        if m.source_column:
            continue
        if not m.default_value:
            continue
        await m.set({
            "default_value": None,
            "status": "suggested",
            "reason": "Gold-derived default removed by user",
        })
        cleared += 1

    remapped = None
    if body.rerun_ai:
        try:
            res = await run_mapping_suggestions(conv)
            remapped = len(res)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning("reset-defaults rerun AI failed: %s", exc)

    return {
        "target_object": obj,
        "rules_forgotten": rules_forgotten,
        "defaults_cleared": cleared,
        "fields": sorted(forget_field_keys) or sorted(want_fields),
        "remapped": remapped,
    }
