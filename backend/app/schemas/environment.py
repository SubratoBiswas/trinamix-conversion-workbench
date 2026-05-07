"""Schemas for environments + environment runs (cutover dashboard)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    name: str
    description: str | None = None
    sort_order: int
    color: str
    sox_controlled: int
    created_at: datetime


class EnvironmentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    environment_id: int
    conversion_id: int
    dataset_id: int | None = None
    status: str
    stage: str | None = None
    record_count: int | None = None
    passed_count: int | None = None
    failed_count: int | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    # Convenience joins
    environment_name: str | None = None
    conversion_name: str | None = None
    dataset_name: str | None = None


class EnvironmentRunCreate(BaseModel):
    """Used when promoting a conversion into a new environment.

    Either `dataset_id` (re-using an existing uploaded dataset) or `clone_from_run_id`
    (continue with the previous env's dataset) — exactly one is expected.
    """
    environment_id: int
    conversion_id: int
    dataset_id: int | None = None
    notes: str | None = None


class EnvironmentRunUpdate(BaseModel):
    status: str | None = None
    stage: str | None = None
    notes: str | None = None
    dataset_id: int | None = None


class CutoverDashboard(BaseModel):
    """Aggregate view returned by /api/projects/{id}/cutover."""
    project_id: int
    project_name: str
    days_to_go_live: int | None = None
    cutover_window_start: datetime | None = None
    cutover_window_end: datetime | None = None
    sox_controlled: bool
    environments: list[dict[str, Any]]
    pipeline_runs: list[dict[str, Any]]