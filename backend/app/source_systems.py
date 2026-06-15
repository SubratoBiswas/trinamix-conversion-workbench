"""Source-system catalog.

The conversion workbench reads from many ERP source systems and always writes
to Oracle Fusion Cloud. The ``SourceSystem`` enum drives:

* The ``Project.source_system`` field (set at project creation).
* The ``Dataset.source_system`` denormalization (inherited from project on
  upload, used by the column-name normalizer).
* The ``LearnedMapping.source_system`` denormalization (key into the cross-
  project Mapping Knowledge Base).
* The ``SourceConnection.source_system`` field (drives which scanner runs).
* The ``Discovery`` pillar metadata (the same six pillars apply across all
  source systems, but the per-pillar query catalog is source-specific).

When a new source system is added here, three things follow:

1. The scanner module under ``app/discovery/<source>_scanner.py`` is built or
   stubbed (mock-mode is acceptable for v1 — the customer's read-only test
   instance plugs into the same interface).
2. The vendor catalog gets a ``<source>:`` section if the source has a
   well-known integration vocabulary.
3. The column-name normalizer gets a per-source rule set so cross-project
   matches don't accidentally cross dissimilar naming conventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceSystemSpec:
    code: str           # canonical short identifier (used as DB value)
    display_name: str   # what humans see in the picker
    family: str         # "erp" | "hcm" | "crm" | "custom"
    has_scanner_v1: bool


# v1 catalog. Codes are stable — do not rename without a migration.
SOURCE_SYSTEMS: tuple[SourceSystemSpec, ...] = (
    SourceSystemSpec("netsuite",   "NetSuite",        "erp",    True),
    SourceSystemSpec("oracle_ebs", "Oracle EBS",      "erp",    True),
    SourceSystemSpec("sap_ecc",    "SAP ECC",         "erp",    False),
    SourceSystemSpec("sap_s4",     "SAP S/4 HANA",    "erp",    False),
    SourceSystemSpec("workday",    "Workday",         "hcm",    False),
    SourceSystemSpec("jde",        "JD Edwards",      "erp",    False),
    SourceSystemSpec("custom",     "Custom / Other",  "custom", False),
)


VALID_CODES: frozenset[str] = frozenset(s.code for s in SOURCE_SYSTEMS)

# Convenience constants for code that references specific sources.
NETSUITE = "netsuite"
ORACLE_EBS = "oracle_ebs"
CUSTOM = "custom"


def spec_for(code: str | None) -> SourceSystemSpec | None:
    if not code:
        return None
    for s in SOURCE_SYSTEMS:
        if s.code == code:
            return s
    return None


def is_valid(code: str | None) -> bool:
    return bool(code) and code in VALID_CODES


def normalize_code(value: str | None) -> str | None:
    """Accept either a canonical code (``"netsuite"``) or a display name
    (``"NetSuite"``) and return the canonical code. Returns None for unknown
    values so callers can raise a typed error.
    """
    if not value:
        return None
    value = value.strip()
    if value in VALID_CODES:
        return value
    lowered = value.lower()
    for s in SOURCE_SYSTEMS:
        if s.display_name.lower() == lowered:
            return s.code
    return None


def display_name_for(code: str | None) -> str | None:
    s = spec_for(code)
    return s.display_name if s else None


def codes_with_scanner() -> Iterable[str]:
    return tuple(s.code for s in SOURCE_SYSTEMS if s.has_scanner_v1)
