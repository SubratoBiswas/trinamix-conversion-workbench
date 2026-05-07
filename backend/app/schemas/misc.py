"""Workflow, dependency, dashboard schemas."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    conversion_id: int | None = None
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    conversion_id: int | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    status: str | None = None


class WorkflowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str | None = None
    conversion_id: int | None = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    status: str
    last_run_at: datetime | None = None
    last_run_summary: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DependencyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    source_object: str
    target_object: str
    relationship_type: str
    description: str | None = None


class DashboardKpis(BaseModel):
    total_datasets: int
    total_templates: int
    total_projects: int
    total_conversions: int
    total_workflows: int
    total_load_runs: int
    pass_rate: float
    fail_rate: float
    recent_projects: list[dict[str, Any]]
    recent_conversions: list[dict[str, Any]]
    recent_load_runs: list[dict[str, Any]]
    project_status_breakdown: list[dict[str, Any]]
    conversion_status_breakdown: list[dict[str, Any]]
    load_status_breakdown: list[dict[str, Any]]
