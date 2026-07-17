"""Seed the Employee HDL loader as a first-class conversion target.

Unlike the FBDI templates (parsed from a shipped .xlsm), the HDL load has no
workbook — its structure is defined in ``hdl_schema``. So we synthesize the same
FBDITemplate / FBDISheet / FBDIField records programmatically: one template
("Employee HDL Import", business object "Employee HDL"), one sheet per HDL
component, and one field per attribute. That makes the object mappable and
selectable in the UI exactly like an FBDI object; the generator (``hdl_output_
service``) recognises the "Employee HDL" business object and emits .dat files
instead of CSVs.

Idempotent + non-destructive: skipped once a template with this business object
(or name) already exists, so re-deploys are a no-op.
"""
from __future__ import annotations

import logging

from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField
from app.services.hdl_schema import (
    HDL_BUSINESS_OBJECT, all_components,
)

logger = logging.getLogger(__name__)

_TEMPLATE_NAME = "Employee HDL Import"
_MODULE = "HCM / Human Capital Management"


def _is_hdl_template(t) -> bool:
    bo = (t.business_object or "").strip().lower()
    if bo:
        return bo == HDL_BUSINESS_OBJECT.lower()
    return (t.name or "").strip().lower() == _TEMPLATE_NAME.lower()


async def ensure_employee_hdl() -> dict:
    """Guarantee the Employee HDL template exists with its component sheets +
    attribute fields. Returns a small status dict. Idempotent."""
    existing = [t for t in await FBDITemplate.find_all().to_list() if _is_hdl_template(t)]
    if existing:
        # Confirm it actually has sheets; if a prior partial seed left it empty,
        # (re)build against the surviving template rather than duplicating it.
        tpl = existing[0]
        sheet_n = await FBDISheet.find(FBDISheet.template_id == tpl.id).count()
        if sheet_n > 0:
            return {"seeded": False, "template_id": str(tpl.id), "sheets": sheet_n,
                    "note": "Employee HDL template already present"}
        target = tpl
    else:
        target = None

    comps = all_components()
    field_total = sum(len(fields) for _, _, fields in comps)

    if target is None:
        target = FBDITemplate(
            name=_TEMPLATE_NAME,
            module=_MODULE,
            business_object=HDL_BUSINESS_OBJECT,
            version="1.0",
            required_field_count=sum(
                1 for _, _, fields in comps for f in fields if f.get("required")
            ),
            status="parsed",
            description=(
                "Oracle HCM Data Loader (HDL) Employee load — Location, Job, "
                "Position, PositionHierarchy and Worker (with PersonName, "
                "PersonEmail, WorkRelationship, WorkTerms, Assignment and "
                "AssignmentSupervisor components). Generated as pipe-delimited "
                ".dat files, not FBDI CSVs."
            ),
        )
        await target.insert()

    seq = 0            # global field sequence (interface order across the load)
    sheet_seq = 0      # contiguous sheet sequence (0,1,2,… per component)
    sheet_docs = 0
    field_docs: list[FBDIField] = []
    for _obj, comp_name, fields in comps:
        sheet = FBDISheet(
            template_id=target.id, sheet_name=comp_name,
            sequence=sheet_seq, field_count=len(fields),
        )
        await sheet.insert()
        sheet_seq += 1
        sheet_docs += 1
        for i, f in enumerate(fields):
            kind = f.get("kind")
            note = {
                "source": f"HDL attribute — mapped from source '{f.get('source')}'.",
                "const": f"HDL structural constant — always '{f.get('value')}'.",
                "key": f"HDL SourceSystemId composite key ('{f.get('prefix')}"
                       f"{f.get('sep', '_')}<key>').",
                "valuemap": f"Value-mapped from source '{f.get('source')}'.",
                "date": f"Date reformatted from source '{f.get('source')}' to YYYY/MM/DD.",
                "manager": f"Manager reference parsed from '{f.get('source')}'.",
                "blank": "Required by Oracle; supplied by business (left blank).",
            }.get(kind, "")
            is_date = kind == "date"
            field_docs.append(FBDIField(
                template_id=target.id, sheet_id=sheet.id,
                field_name=f["name"],
                display_name=(f["name"] + " *") if f.get("required") else f["name"],
                required=bool(f.get("required")),
                data_type="date" if is_date else "text",
                sample_value=str(f.get("value")) if kind == "const" else None,
                validation_notes=note,
                sequence=seq,
            ))
            seq += 1
    if field_docs:
        await FBDIField.insert_many(field_docs)

    logger.info("hdl seed: Employee HDL template — %d component sheets, %d fields",
                sheet_docs, len(field_docs))
    return {"seeded": True, "template_id": str(target.id),
            "sheets": sheet_docs, "fields": len(field_docs)}
