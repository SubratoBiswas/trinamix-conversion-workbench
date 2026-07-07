"""Durable storage for uploaded dataset source files.

Render's container disk is ephemeral (wiped on every redeploy), which loses the
raw dataset extracts and 500s output generation/preview. Dataset files can be
large (tens of MB), so they don't fit an inline Mongo document — they're stored
in GridFS and rehydrated to local disk on demand.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from app.config import settings
from app.database import get_client

logger = logging.getLogger(__name__)

_BUCKET = "dataset_files"


def _bucket() -> AsyncIOMotorGridFSBucket:
    db = get_client()[settings.MONGODB_DB]
    return AsyncIOMotorGridFSBucket(db, bucket_name=_BUCKET)


async def store_dataset_bytes(dataset_id, filename: str | None, contents: bytes) -> None:
    """Persist a dataset's raw bytes in GridFS (durable across redeploys).
    Replaces any prior copy for the same dataset. Best-effort: never breaks upload."""
    try:
        bucket = _bucket()
        async for f in bucket.find({"metadata.dataset_id": str(dataset_id)}):
            try:
                await bucket.delete(f._id)
            except Exception:
                pass
        await bucket.upload_from_stream(
            filename or f"{dataset_id}.dat",
            io.BytesIO(contents),
            metadata={"dataset_id": str(dataset_id)},
        )
    except Exception as exc:
        logger.exception(f"Failed to store dataset bytes in GridFS for {dataset_id}: {exc}")


async def load_dataset_bytes(dataset_id) -> bytes | None:
    """Return the most recently stored bytes for a dataset, or None."""
    try:
        bucket = _bucket()
        newest = None
        async for f in bucket.find({"metadata.dataset_id": str(dataset_id)}).sort("uploadDate", -1):
            newest = f
            break
        if newest is None:
            return None
        stream = await bucket.open_download_stream(newest._id)
        return await stream.read()
    except Exception as exc:
        logger.exception(f"Failed to load dataset bytes from GridFS for {dataset_id}: {exc}")
        return None


async def materialize_dataset_file(ds) -> Path | None:
    """Return a filesystem path to the dataset's raw file for parsing.

    Prefers the on-disk copy; if the ephemeral disk was wiped (e.g. after a
    Render redeploy), rehydrates the bytes from GridFS to a local file and
    returns that (also refreshing ds.file_path). None only when no bytes exist.
    """
    if ds.file_path and Path(ds.file_path).exists():
        return Path(ds.file_path)
    data = await load_dataset_bytes(ds.id)
    if data is None:
        return None
    name = ds.file_name or f"{ds.id}.dat"
    target = settings.upload_path / "datasets" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    try:
        await ds.set({"file_path": str(target)})
    except Exception:
        pass
    return target


async def delete_dataset_bytes(dataset_id) -> None:
    try:
        bucket = _bucket()
        async for f in bucket.find({"metadata.dataset_id": str(dataset_id)}):
            await bucket.delete(f._id)
    except Exception as exc:
        logger.exception(f"Failed to delete dataset bytes for {dataset_id}: {exc}")
