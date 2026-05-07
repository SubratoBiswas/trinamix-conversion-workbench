"""Dataset upload + profiling service."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.models.dataset import Dataset, DatasetColumnProfile
from app.parsers import parse_tabular, profile_dataframe


ALLOWED_DATASET_EXTS = {".csv", ".xlsx", ".xls"}


def save_upload(upload: UploadFile, subdir: str = "datasets") -> tuple[Path, str]:
    target_dir = settings.upload_path / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "upload").name
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


def create_dataset_from_upload(
    db: Session, upload: UploadFile, name: str | None, description: str | None
) -> Dataset:
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in ALLOWED_DATASET_EXTS:
        raise ValueError(f"Unsupported file extension: {ext}")
    file_path, stored_name = save_upload(upload)

    df = parse_tabular(file_path, file_type=ext.lstrip("."))
    profiles = profile_dataframe(df)

    ds = Dataset(
        name=name or Path(upload.filename or stored_name).stem,
        description=description,
        file_name=stored_name,
        file_path=str(file_path),
        file_type=ext.lstrip("."),
        row_count=int(len(df)),
        column_count=int(len(df.columns)),
        status="profiled",
    )
    db.add(ds)
    db.flush()
    for prof in profiles:
        db.add(DatasetColumnProfile(dataset_id=ds.id, **prof))
    db.commit()
    db.refresh(ds)
    return ds


def get_dataset_preview(ds: Dataset, limit: int = 50) -> dict[str, Any]:
    df = parse_tabular(ds.file_path, file_type=ds.file_type)
    head = df.head(limit)
    return {
        "columns": list(head.columns.astype(str)),
        "rows": head.fillna("").to_dict(orient="records"),
        "total_rows": int(len(df)),
    }
