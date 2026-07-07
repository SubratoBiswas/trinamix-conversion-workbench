"""FBDI template upload, parse, and metadata correction service."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import UploadFile

logger = logging.getLogger(__name__)

from app.config import settings
from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField, FBDITemplateFile
from app.parsers import parse_fbdi_template


ALLOWED_FBDI_EXTS = {".xlsx", ".xlsm", ".xls"}


def _save_bytes(filename: str, contents: bytes) -> tuple[Path, str]:
    """Save raw bytes to upload dir, avoiding filename collisions."""
    target_dir = settings.upload_path / "fbdi"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    target = target_dir / safe_name
    counter = 1
    while target.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        target = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    target.write_bytes(contents)
    return target, target.name


async def store_template_bytes(tpl: FBDITemplate, filename: str, contents: bytes) -> None:
    """Persist the raw uploaded bytes in MongoDB (durable across redeploys).
    One record per template; replaces any prior copy."""
    try:
        await FBDITemplateFile.find(FBDITemplateFile.template_id == tpl.id).delete()
        await FBDITemplateFile(
            template_id=tpl.id,
            file_name=Path(filename).name,
            content=contents,
            size=len(contents),
        ).insert()
    except Exception as exc:  # storage is best-effort; never break the upload
        logger.exception(f"Failed to store template bytes in Mongo for {tpl.id}: {exc}")


async def materialize_template_file(tpl: FBDITemplate) -> Path | None:
    """Return a filesystem path to the template's raw file for parsing.

    Prefers the on-disk copy; if the ephemeral disk was wiped (e.g. after a
    Render redeploy), rehydrates the bytes stored in MongoDB to a local file and
    returns that. Also refreshes tpl.file_path so later reads hit the disk copy.
    Returns None only when no bytes exist anywhere.
    """
    if tpl.file_path and Path(tpl.file_path).exists():
        return Path(tpl.file_path)
    rec = await FBDITemplateFile.find_one(FBDITemplateFile.template_id == tpl.id)
    if not rec or not rec.content:
        return None
    name = rec.file_name or tpl.file_name or f"{tpl.id}.xlsx"
    target = settings.upload_path / "fbdi" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(rec.content)
    try:
        await tpl.set({"file_path": str(target), "file_name": target.name})
    except Exception:
        pass
    return target


async def create_template_from_upload(
    upload: UploadFile,
    name: str | None,
    module: str | None,
    business_object: str | None,
) -> FBDITemplate:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_FBDI_EXTS:
        raise ValueError(f"Unsupported FBDI file extension: {ext}")

    # Must use await upload.read() — shutil.copyfileobj on upload.file is
    # unreliable in async FastAPI (buffer may already be consumed).
    contents = await upload.read()
    file_path, stored_name = _save_bytes(upload.filename or "template.xlsx", contents)
    file_size = len(contents)
    logger.info(f"FBDI upload saved: {file_path} size={file_size}")

    try:
        parsed = parse_fbdi_template(file_path)
    except Exception as exc:
        logger.exception(f"FBDI parse error for {file_path}: {exc}")
        parsed = {"business_object": None, "description": None, "sheets": [], "fields": []}
    logger.info(f"FBDI parse result: {len(parsed['fields'])} fields, {len(parsed['sheets'])} sheets")

    required_field_count = sum(1 for f in parsed["fields"] if f.get("required"))
    tpl = FBDITemplate(
        name=name or Path(upload.filename or stored_name).stem,
        module=module,
        business_object=business_object or parsed.get("business_object"),
        version="1.0",
        required_field_count=required_field_count,
        file_name=stored_name,
        file_path=str(file_path),
        status="parsed" if parsed["fields"] else "manual",
        description=parsed.get("description"),
    )
    await tpl.insert()
    # Durable copy in MongoDB so re-parse still works after a redeploy wipes disk.
    await store_template_bytes(tpl, stored_name, contents)

    sheet_id_by_name: dict[str, object] = {}
    for s in parsed["sheets"]:
        sheet = FBDISheet(
            template_id=tpl.id,
            sheet_name=s["sheet_name"],
            sequence=s["sequence"],
            field_count=s["field_count"],
        )
        await sheet.insert()
        sheet_id_by_name[s["sheet_name"]] = sheet.id

    for f in parsed["fields"]:
        sheet_name = f.pop("sheet_name", None)
        sheet_id = sheet_id_by_name.get(sheet_name)
        if sheet_id is None:
            continue
        await FBDIField(template_id=tpl.id, sheet_id=sheet_id, **f).insert()

    return tpl
