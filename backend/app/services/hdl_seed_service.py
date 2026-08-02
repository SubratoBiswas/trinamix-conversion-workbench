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
import re

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


def _field_docs_for(template_id, sheet_id, fields, start_seq: int) -> list:
    """Build the FBDIField rows for one component. Extracted so the reconcile path
    creates fields the SAME way the fresh-seed path does — two implementations of
    'what a component looks like' would drift, and the one nobody exercises is the
    one that would be wrong."""
    out, seq = [], start_seq
    for f in fields:
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
        out.append(FBDIField(
            template_id=template_id, sheet_id=sheet_id,
            field_name=f["name"],
            display_name=(f["name"] + " *") if f.get("required") else f["name"],
            required=bool(f.get("required")),
            data_type="date" if kind == "date" else "text",
            sample_value=str(f.get("value")) if kind == "const" else None,
            validation_notes=note,
            sequence=seq,
        ))
        seq += 1
    return out


async def _add_components(tpl, comps, *, start_order: int = 0) -> list[str]:
    """Add component sheets (and their fields) to an EXISTING template."""
    seq = await FBDIField.find(FBDIField.template_id == tpl.id).count()
    added, docs = [], []
    for i, (_obj, comp_name, fields) in enumerate(comps):
        sheet = FBDISheet(template_id=tpl.id, sheet_name=comp_name,
                          sequence=start_order + i, field_count=len(fields))
        await sheet.insert()
        docs.extend(_field_docs_for(tpl.id, sheet.id, fields, seq))
        seq += len(fields)
        added.append(comp_name)
    if docs:
        await FBDIField.insert_many(docs)
    return added


async def ensure_employee_hdl() -> dict:
    """Guarantee the Employee HDL template exists with its component sheets +
    attribute fields. Returns a small status dict. Idempotent."""
    existing = [t for t in await FBDITemplate.find_all().to_list() if _is_hdl_template(t)]
    if existing:
        # Confirm it actually has sheets; if a prior partial seed left it empty,
        # (re)build against the surviving template rather than duplicating it.
        tpl = existing[0]
        sheet_n = await FBDISheet.find(FBDISheet.template_id == tpl.id).count()
        # RECONCILE, don't skip. "Has at least one sheet" is not the same as "has the
        # sheets the schema declares", and treating them as the same is how a template
        # seeded when hdl_schema had two objects stayed at two objects after the schema
        # grew to six. The download then shipped one workbook with the two Worker tabs,
        # which reads as a generation failure — the objects were not missing, they had
        # never been created. A seeder that refuses to look at an existing row can only
        # ever be right on a fresh database.
        if sheet_n > 0:
            _have = {(sh.sheet_name or "").strip().lower()
                     for sh in await FBDISheet.find(
                         FBDISheet.template_id == tpl.id).to_list()}
            _want = [c for c in all_components()
                     if (c[1] or "").strip().lower() not in _have]
            if not _want:
                return {"seeded": False, "template_id": str(tpl.id), "sheets": sheet_n,
                        "note": "Employee HDL template already complete"}
            added = await _add_components(tpl, _want, start_order=sheet_n)
            return {"seeded": False, "reconciled": True, "template_id": str(tpl.id),
                    "sheets": sheet_n + len(_want), "added_sheets": added,
                    "note": (f"Added {len(added)} component sheet(s) the schema declares "
                             f"and the template did not have: {', '.join(added)}")}
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
            is_global=True,  # Oracle-standard HDL template applies to every client
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

_HCM_LIKE = re.compile(r"(worker|hcm|employee|hdl)", re.I)


async def consolidate_employee_hdl() -> dict:
    """One HDL template for the Employee object, and every conversion pointed at it.

    Analyst, 02-Aug, after the generated workbook kept arriving with two tabs:
    "Re-bind the Employee conversion to the 6-object template and delete Worker HCM
    as a duplicate."

    The filename was the diagnosis. The download was ``Worker_HCM.xlsx`` — that is a
    TEMPLATE NAME, and that template carries two sheets. So reseeding the Employee HDL
    template could never have fixed it: the conversion was never bound to the template
    being repaired. Two templates claimed the same object, one of them was wrong, and
    nothing on any screen said which one a conversion was using.

    RETIRED, NOT DELETED. Generated outputs and mapping rows reference template_id, and
    hard-deleting the row would orphan every artifact produced from it — the history
    would stop explaining itself, which is worse than a stale row nobody selects. The
    duplicate is marked status="retired" and dropped out of the pickers instead.

    Reports what it moved and what it retired, by name, because a silent rebind of live
    conversions is exactly the kind of change that has to be auditable after the fact.
    """
    canon_res = await ensure_employee_hdl()
    canon = None
    for t in await FBDITemplate.find_all().to_list():
        if _is_hdl_template(t):
            canon = t
            break
    if canon is None:
        return {"error": "no canonical Employee HDL template"}
    canon_sheets = await FBDISheet.find(FBDISheet.template_id == canon.id).count()

    # Every OTHER template whose name reads like an HCM object. Matching on the name
    # rather than a hardcoded "Worker HCM" so a second duplicate under another spelling
    # is caught too — one-off cleanups that only know one bad name get run twice.
    dupes = [t for t in await FBDITemplate.find_all().to_list()
             if t.id != canon.id
             and (t.status or "") != "retired"
             and _HCM_LIKE.search(f"{t.name or ''} {t.business_object or ''}")]
    if not dupes:
        return {"canonical": canon.name, "canonical_sheets": canon_sheets,
                "rebound": 0, "retired": [], "seed": canon_res,
                "note": "no duplicate HCM template found"}

    from app.models.conversion import Conversion
    dupe_ids = {t.id for t in dupes}
    moved = []
    for c in await Conversion.find_all().to_list():
        if c.template_id in dupe_ids:
            await c.set({"template_id": canon.id})
            moved.append(c.name)
    retired = []
    for t in dupes:
        n = await FBDISheet.find(FBDISheet.template_id == t.id).count()
        await t.set({"status": "retired"})
        retired.append(f"{t.name} ({n} sheet{'' if n == 1 else 's'})")
    logger.info("hdl consolidate: %d conversion(s) rebound to %s; retired %s",
                len(moved), canon.name, ", ".join(retired))
    return {"canonical": canon.name, "canonical_sheets": canon_sheets,
            "rebound": len(moved), "conversions": moved[:25],
            "retired": retired, "seed": canon_res}
