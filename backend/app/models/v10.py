"""
v10 feature models — new MongoDB collections added on top of the existing stack.

Collections:
  source_connections     – credentials + config for legacy source systems
  discovery_runs         – async scan jobs triggered against a source connection
  discovered_objects     – individual objects/tables found during a discovery run
  audit_events           – immutable event log (append-only)
  coa_structures         – Chart-of-Account structure definitions per project
  coa_segments           – individual segment definitions within a COA structure
  coa_value_crosswalks   – legacy-value → Fusion-value crosswalk rows
  issues                 – project-level issues tracker
  risks                  – project-level risks register
  sign_offs              – governance sign-off checkpoints
  dress_rehearsals       – scheduled dress rehearsal runs
  cutover_tasks          – individual tasks within a dress rehearsal / cutover plan
  reconciliation_checks  – post-load reconciliation check results
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field


# ─────────────────────────────────────────────
# Source connections & discovery
# ─────────────────────────────────────────────

class SourceConnection(Document):
    """Credentials + config for a legacy source system (encrypted at rest)."""
    project_id: Optional[PydanticObjectId] = None
    system_type: str                          # netsuite | oracle_ebs | sap | dynamics | manual
    name: str
    host: Optional[str] = None
    port: Optional[int] = None
    service_name: Optional[str] = None       # Oracle SID / service name
    username: Optional[str] = None
    # credential stored as Fernet-encrypted bytes (base-64 string)
    encrypted_password: Optional[str] = None
    account_id: Optional[str] = None         # NetSuite account ID
    consumer_key: Optional[str] = None
    # For REST-based sources (NetSuite REST / Dynamics OData)
    client_id: Optional[str] = None
    encrypted_client_secret: Optional[str] = None
    token_url: Optional[str] = None
    base_url: Optional[str] = None
    # Extra per-source metadata (instance_name, edition, etc.)
    connection_metadata: Optional[Dict[str, Any]] = None
    # Auth type label (mock | db_basic | db_wallet | oauth1_tba | oauth2_client_credentials)
    auth_type: Optional[str] = None
    # Test status
    last_tested_at: Optional[datetime] = None
    last_test_ok: Optional[bool] = None
    last_test_error: Optional[str] = None
    # Structured test details stored after last probe run
    last_test_details: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "source_connections"
        indexes = ["project_id", "system_type"]


class DiscoveryRun(Document):
    """An async scan job run against a SourceConnection."""
    connection_id: PydanticObjectId
    project_id: Optional[PydanticObjectId] = None
    status: str = "pending"                  # pending | running | completed | failed
    modules_requested: List[str] = Field(default_factory=list)
    objects_found: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "discovery_runs"
        indexes = ["connection_id", "project_id"]


class DiscoveredObject(Document):
    """An individual table/view/endpoint found during a DiscoveryRun."""
    run_id: PydanticObjectId
    connection_id: PydanticObjectId
    project_id: Optional[PydanticObjectId] = None
    module: Optional[str] = None             # e.g. "GL", "AP", "AR"
    object_name: str                         # table/view/endpoint name
    object_type: str = "table"              # table | view | api_endpoint
    row_count: Optional[int] = None
    column_count: Optional[int] = None
    columns: List[Dict[str, Any]] = Field(default_factory=list)
    # suggested Oracle Fusion mapping
    suggested_fbdi_object: Optional[str] = None
    suggestion_confidence: float = 0.0
    selected: bool = False                   # user marked this for migration
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "discovered_objects"
        indexes = ["run_id", "project_id"]


# ─────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────

class AuditEvent(Document):
    """Immutable audit log — never update, only insert."""
    project_id: Optional[PydanticObjectId] = None
    conversion_id: Optional[PydanticObjectId] = None
    actor: str = "system"                    # username or "system"
    action: str                              # e.g. "mapping.approved", "output.generated"
    entity_type: Optional[str] = None       # "mapping" | "project" | "conversion" etc.
    entity_id: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    occurred_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "audit_events"
        indexes = ["project_id", "conversion_id", "actor", "action"]


# ─────────────────────────────────────────────
# Chart of Accounts (COA)
# ─────────────────────────────────────────────

class CoaStructure(Document):
    """Top-level COA structure definition per project."""
    project_id: PydanticObjectId
    name: str
    description: Optional[str] = None
    legacy_system: Optional[str] = None     # source system this COA came from
    segment_count: int = 0
    total_values: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "coa_structures"
        indexes = ["project_id"]


class CoaSegment(Document):
    """A single segment within a COA structure (e.g. Company, Cost Center, Account)."""
    structure_id: PydanticObjectId
    project_id: PydanticObjectId
    segment_name: str
    segment_label: Optional[str] = None
    segment_order: int = 0
    value_set_name: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "coa_segments"
        indexes = ["structure_id", "project_id"]


class CoaValueCrosswalk(Document):
    """Legacy segment value → Oracle Fusion segment value mapping."""
    segment_id: PydanticObjectId
    structure_id: PydanticObjectId
    project_id: PydanticObjectId
    legacy_value: str
    legacy_description: Optional[str] = None
    fusion_value: Optional[str] = None
    fusion_description: Optional[str] = None
    status: str = "pending"                  # pending | mapped | excluded
    mapped_by: Optional[str] = None
    mapped_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "coa_value_crosswalks"
        indexes = ["segment_id", "project_id"]


# ─────────────────────────────────────────────
# Governance — Issues & Risks
# ─────────────────────────────────────────────

class Issue(Document):
    """Project-level issue tracked to resolution."""
    project_id: PydanticObjectId
    title: str
    description: Optional[str] = None
    severity: str = "medium"                 # low | medium | high | critical
    status: str = "open"                     # open | in_progress | resolved | closed
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    created_by: str = "system"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "issues"
        indexes = ["project_id", "status"]


class Risk(Document):
    """Project-level risk register entry."""
    project_id: PydanticObjectId
    title: str
    description: Optional[str] = None
    likelihood: str = "medium"               # low | medium | high
    impact: str = "medium"                   # low | medium | high
    status: str = "identified"               # identified | mitigating | accepted | closed
    mitigation_plan: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    created_by: str = "system"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "risks"
        indexes = ["project_id", "status"]


# ─────────────────────────────────────────────
# Governance — Sign-offs & Dress Rehearsals
# ─────────────────────────────────────────────

class SignOff(Document):
    """Governance checkpoint requiring explicit sign-off."""
    project_id: PydanticObjectId
    conversion_id: Optional[PydanticObjectId] = None
    checkpoint: str                          # e.g. "mapping_approved", "output_validated"
    signed_off_by: Optional[str] = None
    signed_off_at: Optional[datetime] = None
    status: str = "pending"                  # pending | signed | rejected
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "sign_offs"
        indexes = ["project_id", "checkpoint"]


class DressRehearsal(Document):
    """A scheduled dress rehearsal run for the full migration pipeline."""
    project_id: PydanticObjectId
    name: str
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    status: str = "planned"                  # planned | running | completed | failed
    outcome: Optional[str] = None           # pass | fail | partial
    records_processed: int = 0
    records_failed: int = 0
    issues_found: int = 0
    notes: Optional[str] = None
    conducted_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "dress_rehearsals"
        indexes = ["project_id"]


class CutoverTask(Document):
    """An individual task in a cutover / dress rehearsal plan."""
    project_id: PydanticObjectId
    rehearsal_id: Optional[PydanticObjectId] = None
    title: str
    description: Optional[str] = None
    sequence: int = 0                       # display/execution order
    owner: Optional[str] = None
    estimated_minutes: Optional[int] = None
    actual_minutes: Optional[int] = None
    status: str = "pending"                 # pending | in_progress | completed | skipped | failed
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "cutover_tasks"
        indexes = ["project_id", "rehearsal_id"]


# ─────────────────────────────────────────────
# Reconciliation
# ─────────────────────────────────────────────

class ReconciliationCheck(Document):
    """Post-load reconciliation result comparing source vs Fusion counts/totals."""
    project_id: PydanticObjectId
    conversion_id: Optional[PydanticObjectId] = None
    load_run_id: Optional[PydanticObjectId] = None
    check_name: str
    check_type: str = "count"               # count | sum | hash | sample
    source_value: Optional[str] = None
    fusion_value: Optional[str] = None
    tolerance: float = 0.0
    passed: Optional[bool] = None
    variance: Optional[float] = None
    notes: Optional[str] = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "reconciliation_checks"
        indexes = ["project_id", "conversion_id"]
