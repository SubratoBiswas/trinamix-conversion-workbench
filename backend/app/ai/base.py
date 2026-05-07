"""Abstract mapping provider interface and shared dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SourceColumn:
    name: str
    inferred_type: str
    sample_values: list[str] = field(default_factory=list)
    null_percent: float = 0.0
    distinct_count: int = 0
    pattern_summary: str | None = None


@dataclass
class TargetField:
    id: int
    field_name: str
    description: str | None
    data_type: str | None
    max_length: int | None
    required: bool


@dataclass
class MappingSuggestion:
    target_field_id: int
    target_field_name: str
    source_column: str | None
    confidence: float  # 0.0 .. 1.0
    reason: str
    suggested_transformation: dict[str, Any] | None = None
    review_required: bool = True


class MappingProvider(Protocol):
    name: str

    def suggest_mappings(
        self,
        source_columns: list[SourceColumn],
        target_fields: list[TargetField],
    ) -> list[MappingSuggestion]: ...
