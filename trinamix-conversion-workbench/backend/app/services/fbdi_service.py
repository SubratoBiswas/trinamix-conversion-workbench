"""FBDI template upload, parse, and metadata correction service."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from app.config import settings
from app.models.fbdi import FBDITemplate, FBDISheet, FBDIField
from app.parsers import parse_fbdi_template


ALLOWED_FBDI_EXTS = {".xlsx", ".xlsm", ".xls"}


def _save_template_file(upload: UploadFile) -> tuple[Path, str]:
    target_dir = settings.upload_path / "fbdi"
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "template.xlsm").name
    target = target_dir / safe_name
    counter = 1
    while target.exists():
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        target = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    with open(target, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    upload.file.close()
    return target, target.name


async def create_template_from_upload(
    upload: UploadFile,
    name: str | None,
    module: str | None,
    business_object: str | None,
) -> FBDITemplate:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_FBDI_EXTS:
        raise ValueError(f"Unsupported FBDI file extension: {ext}")
    file_path, stored_name = _save_template_file(upload)
    parsed = parse_fbdi_template(file_path)

    tpl = FBDITemplate(
        name=name or Path(upload.filename or stored_name).stem,
        module=module,
        business_object=business_object or parsed.get("business_object"),
        version="1.0",
        file_name=stored_name,
        file_path=str(file_path),
        status="parsed" if parsed["fields"] else "manual",
        description=parsed.get("description"),
    )
    await tpl.insert()

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
