"""Oracle FBDI template metadata models."""
from datetime import datetime
from typing import Any, Optional
from beanie import Document, PydanticObjectId
from pydantic import Field

class FBDITemplate(Document):
    name: str
    module: Optional[str] = None
    business_object: Optional[str] = None
    # Tenant scope. The bundled Oracle FBDI/HDL templates are is_global=True (they
    # are Oracle-standard, client-agnostic). A client could upload a bespoke
    # template scoped to itself (client_id set, is_global=False).
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False
    tier: str = "T1"
    phase: str = "Blueprint"
    required_field_count: int = 0
    version: str = "1.0"
    file_name: Optional[str] = None
    file_path: Optional[str] = None
    status: str = "parsed"
    description: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    # WHEN THE TEMPLATE ITSELF LAST CHANGED — reparsed, reconciled, sheets added.
    #
    # Analyst, 02-Aug: "for templates and mappings also have a date time of when it
    # was updated, use the latest one always, like learnings and mappings." That rule
    # already governs the learning library and it is what templates never had: on
    # 02-Aug SEVENTEEN templates claimed the Employee object, the resolver took
    # whichever the database returned first, and a two-sheet "Worker HCM" beat an
    # eleven-component "Employee HDL Import" for a week. Nothing was wrong with any
    # single row; there was simply no answer to "which of these is current".
    #
    # uploaded_at cannot serve: it records when the FILE arrived, so a template
    # reconciled today still looks as old as the day it was first seeded. This moves
    # only when something CHANGES the template — never on a read, and never on a
    # startup pass that finds nothing to do, because a timestamp that every redeploy
    # re-stamps inverts the very precedence it exists to express. That is not
    # hypothetical: it is exactly what captured_at did to the learnings layer.
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "fbdi_templates"

class FBDITemplateFile(Document):
    """Durable copy of an uploaded template's raw bytes, stored in MongoDB so it
    survives Render redeploys (the container disk is ephemeral). FBDI templates
    are small (well under the 16MB BSON doc limit), so inline binary is fine."""
    template_id: PydanticObjectId
    file_name: Optional[str] = None
    content: bytes
    size: int = 0
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "fbdi_template_files"
        indexes = ["template_id"]

class FBDISheet(Document):
    template_id: PydanticObjectId
    sheet_name: str
    sequence: int = 0
    field_count: int = 0

    class Settings:
        name = "fbdi_sheets"
        indexes = ["template_id"]

class FBDIField(Document):
    template_id: PydanticObjectId
    sheet_id: PydanticObjectId
    field_name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    required: bool = False
    data_type: Optional[str] = None
    max_length: Optional[int] = None
    format_mask: Optional[str] = None
    sample_value: Optional[str] = None
    lookup_type: Optional[str] = None
    validation_notes: Optional[str] = None
    # List of values the destination accepts, e.g.
    # [{"code": "1", "meaning": "Not planned"}, {"code": "3", "meaning": "MRP planned"}].
    # Used for value-aware mapping and crosswalk (value-pair) recommendations.
    allowed_values: list[dict] = Field(default_factory=list)
    # What Fusion defaults this field to when left blank (FBDI behaviour).
    default_if_blank: Optional[str] = None
    sequence: int = 0
    required_modules: list[str] = Field(default_factory=list)
    # ── Mined from the template's own header-cell comment (template_comments.py) ──
    # Oracle publishes the database truth for every column in a comment tooltip:
    # "BATCH_ID / NOT NULL / NUMBER (18)". The parser never read them, so on the
    # tabular templates — which the real Oracle generation workbooks are — data_type
    # came from whether one sample row happened to hold a number, and max_length,
    # format_mask and allowed_values were empty. The validation engine already knew
    # how to check length, numeric, date and value-set; it had nothing to check WITH.
    # None of these is inferred: each is a transcription of what Oracle wrote, which
    # is why it is safe to enforce and why comment_text is kept for audit.
    db_column: Optional[str] = None          # e.g. BATCH_ID
    nullable: Optional[bool] = None          # False when the comment says NOT NULL
    precision: Optional[int] = None          # NUMBER(18) -> 18
    scale: Optional[int] = None              # NUMBER(18,2) -> 2
    # "This column is not used. Do not provide a value for this column." — Oracle
    # says this on 855 of the Customer template's 1,253 columns. Populating one is
    # worth telling the analyst about, and it is the authority for shipping it blank.
    do_not_populate: bool = False
    import_actions: Optional[str] = None     # "CREATE, UPDATE"
    comment_text: Optional[str] = None       # verbatim, so a reviewer can check me

    class Settings:
        name = "fbdi_fields"
        indexes = ["template_id", "sheet_id"]


class GoldStandard(Document):
    """A client's approved FBDI output, held in a project-independent library.

    A gold file is knowledge about an OBJECT (how a Supplier load should look), not
    about one engagement — so it lives here rather than inside a conversion. On
    upload we identify which template it belongs to by header overlap, derive the
    reusable rules (constant defaults, fields gold deliberately leaves blank, and
    source→target column mappings when a paired source extract is supplied), and
    write them as global LearnedMappings. Every future conversion of that object
    picks them up automatically at generate.

    Raw bytes are kept in Mongo (like FBDITemplateFile) so a re-learn survives a
    redeploy and the file can be re-applied after the rule engine changes.
    """
    name: str
    target_object: Optional[str] = None
    template_id: Optional[PydanticObjectId] = None
    template_name: Optional[str] = None
    # Tenant scope. A gold reference standard is a client's approved output, so it
    # is client-scoped (is_global=False, client_id set). Never global.
    client_id: Optional[PydanticObjectId] = None
    is_global: bool = False

    file_name: Optional[str] = None
    content: Optional[bytes] = None
    size: int = 0

    # Optional paired source extract — its presence is what unlocks learning
    # source→target column mappings (without it we can still learn defaults and
    # suppressions, which need only the gold file).
    source_file_name: Optional[str] = None
    source_content: Optional[bytes] = None

    match_confidence: float = 0.0
    rows: int = 0
    defaults_learned: int = 0
    suppressed_learned: int = 0
    mappings_learned: int = 0
    status: str = "learned"          # learned | unmatched | error
    note: Optional[str] = None

    uploaded_by: Optional[str] = None
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    learned_at: Optional[datetime] = None

    class Settings:
        name = "gold_standards"
        indexes = ["target_object", "template_id"]


class OracleLookup(Document):
    """One lookup code from the customer's own Fusion instance.

    Populated by importing a Manage Standard Lookups export. This is the ONLY
    authoritative source for the ~45 lookup types the FBDI templates reference by
    name but don't publish (EGP_MATERIAL_PLANNING, EGP_SOURCE_TYPES, …) — those
    codes are instance-configurable, so anything we'd otherwise seed is a guess.

    Once imported, these codes are written onto the matching FBDIFields'
    ``allowed_values`` with ``verified=True``, which flips the column from
    "unverified — passing values through" to fully mapped and validated.
    """
    lookup_type: str
    code: str
    meaning: Optional[str] = None
    description: Optional[str] = None
    enabled: bool = True
    source: str = "instance_import"   # instance_import | oracle_standard
    imported_by: Optional[str] = None
    imported_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "oracle_lookups"
        indexes = ["lookup_type", "code"]
