"""Mapping orchestration service (async/Beanie)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId

from app.ai import get_mapping_provider
from app.ai.base import SourceColumn, TargetField
from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.conversion import Conversion
from app.parsers import parse_tabular


async def _source_columns_for(dataset: Dataset) -> list[SourceColumn]:
    profs = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == dataset.id
    ).sort(+DatasetColumnProfile.position).to_list()
    return [
        SourceColumn(
            name=p.column_name,
            inferred_type=p.inferred_type or "string",
            sample_values=[str(v) for v in (p.sample_values or [])],
            null_percent=p.null_percent or 0.0,
            distinct_count=p.distinct_count or 0,
            pattern_summary=p.pattern_summary,
        )
        for p in profs
    ]


async def _target_fields_for(template: FBDITemplate) -> list[TargetField]:
    fields = await FBDIField.find(
        FBDIField.template_id == template.id
    ).sort(+FBDIField.sequence).to_list()
    return [
        TargetField(
            id=str(f.id),
            field_name=f.field_name,
            description=f.description,
            data_type=f.data_type,
            max_length=f.max_length,
            required=bool(f.required),
        )
        for f in fields
    ]


async def _source_columns_for_ebs(table_name: str) -> list[SourceColumn]:
    """Fetch column metadata from the live Oracle EBS connection for *table_name*.

    Falls back to an empty list if no EBS connection is configured / reachable.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        from app.models.v10 import SourceConnection
        conn = await SourceConnection.find_one(
            {"source_type": "oracle_ebs", "status": "connected"}
        )
        if conn is None:
            log.debug("_source_columns_for_ebs: no connected EBS connection found")
            return []

        import jaydebeapi
        password = conn.encrypted_password or ""
        if password.startswith("PLAIN:"):
            password = password[6:]

        host = conn.host or "localhost"
        port = conn.port or 1521
        service = conn.service_name or conn.database or "EBSDB"
        jdbc_url = f"jdbc:oracle:thin:@{host}:{port}/{service}"

        db = jaydebeapi.connect(
            "oracle.jdbc.OracleDriver",
            jdbc_url,
            [conn.username, password],
            "/app/ojdbc11.jar",
        )
        cur = db.cursor()
        cur.execute("""
            SELECT column_name, data_type, nullable, num_distinct
            FROM all_tab_columns
            WHERE table_name = UPPER(?)
            ORDER BY column_id
        """, [table_name.upper()])
        rows = cur.fetchall()
        cur.close()
        db.close()

        return [
            SourceColumn(
                name=col_name,
                inferred_type=data_type.lower() if data_type else "string",
                sample_values=[],
                null_percent=0.0 if nullable == "N" else 50.0,
                distinct_count=int(num_distinct or 0),
                pattern_summary=None,
            )
            for col_name, data_type, nullable, num_distinct in rows
        ]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            f"_source_columns_for_ebs: failed for '{table_name}': {exc}"
        )
        return []


async def run_mapping_suggestions(conversion: Conversion) -> list[MappingSuggestion]:
    template = await FBDITemplate.get(conversion.template_id)

    # Determine source columns: EBS live query or static dataset
    if getattr(conversion, "source_type", "dataset") == "ebs":
        table = getattr(conversion, "ebs_table_hint", "") or ""
        sources = await _source_columns_for_ebs(table) if table else []
        if not sources:
            # EBS unreachable or no table hint — nothing to map
            import logging
            logging.getLogger(__name__).warning(
                f"run_mapping_suggestions: EBS source mode but no columns for "
                f"table='{table}' on conversion {conversion.id} — skipping"
            )
            return []
    else:
        if not conversion.dataset_id:
            return []
        dataset = await Dataset.get(conversion.dataset_id)
        sources = await _source_columns_for(dataset)

    targets = await _target_fields_for(template)
    provider = get_mapping_provider()
    ai_results = provider.suggest_mappings(sources, targets)

    existing = {
        m.target_field_id: m
        for m in await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion.id
        ).to_list()
    }

    saved: list[MappingSuggestion] = []
    for s in ai_results:
        tfid = PydanticObjectId(str(s.target_field_id))
        m = existing.get(tfid)
        if m and m.status in ("approved", "rejected", "overridden", "not_applicable"):
            saved.append(m)
            continue
        if m:
            await m.set({
                "source_column": s.source_column, "confidence": s.confidence,
                "reason": s.reason, "suggested_transformation": s.suggested_transformation,
                "review_required": 1 if s.review_required else 0,
                "status": "suggested", "updated_at": datetime.utcnow(),
            })
        else:
            m = MappingSuggestion(
                conversion_id=conversion.id, target_field_id=tfid,
                source_column=s.source_column, confidence=s.confidence,
                reason=s.reason, suggested_transformation=s.suggested_transformation,
                review_required=1 if s.review_required else 0, status="suggested",
            )
            await m.insert()
        saved.append(m)

    await conversion.set({"status": "mapping_suggested", "updated_at": datetime.utcnow()})
    from app.services.learning_service import apply_learned_to_conversion
    await apply_learned_to_conversion(conversion, saved)
    return saved


async def enrich_mapping_with_samples(
    conversion: Conversion, mappings: list[MappingSuggestion]
) -> list[dict[str, Any]]:
    dataset = await Dataset.get(conversion.dataset_id) if conversion.dataset_id else None
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    try:
        df = parse_tabular(dataset.file_path, file_type=dataset.file_type) if dataset else None
    except Exception:
        df = None
    if template:
        fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
    else:
        fields = []
    fields_by_id = {f.id: f for f in fields}
    out: list[dict[str, Any]] = []
    for m in mappings:
        tgt = fields_by_id.get(m.target_field_id)
        sample_src: list[Any] = []
        if df is not None and m.source_column and m.source_column in df.columns:
            sample_src = [str(v) for v in df[m.source_column].astype(str).head(5).tolist()]
        out.append({
            "id": str(m.id), "conversion_id": str(m.conversion_id),
            "target_field_id": str(m.target_field_id),
            "target_field_name": tgt.field_name if tgt else None,
            "target_required": bool(tgt.required) if tgt else False,
            "target_data_type": tgt.data_type if tgt else None,
            "target_max_length": tgt.max_length if tgt else None,
            "source_column": m.source_column, "confidence": m.confidence,
            "reason": m.reason, "suggested_transformation": m.suggested_transformation,
            "review_required": m.review_required, "status": m.status,
            "default_value": m.default_value, "comment": m.comment,
            "approved_by": m.approved_by, "approved_at": m.approved_at,
            "sample_source_values": sample_src, "sample_converted_values": [],
        })
    return out
