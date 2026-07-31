"""Oracle HCM Data Loader (HDL) — Employee load schema.

HDL is NOT FBDI. Where FBDI is a set of CSV interface sheets loaded through an
Excel template, HDL is a set of pipe-delimited ``.dat`` files, one per top-level
business object, each containing one or more component blocks. Every block is a
``METADATA|<Component>|attr1|attr2|...`` header line followed by one
``MERGE|<Component>|val1|val2|...`` line per record. Components are linked by a
``SourceSystemId`` composite key (``<Owner>_<naturalKey>``) rather than by the
running-number glue FBDI uses.

This module is the single source of truth for the Employee HDL load, derived from
the NextPower "Employee HDL Field Mapping" doc (v3). It drives BOTH:

* template seeding (``hdl_seed_service.ensure_employee_hdl``) — the components and
  their attributes become FBDISheet / FBDIField rows so the object is a first-class
  conversion target that can be mapped in the UI; and
* generation (``hdl_output_service.generate_hdl_artifact``) — each field's ``kind``
  tells the writer how to fill it (from a source column, a constant, a derived key,
  a value map, or a date reformat).

Field ``kind``:
  * ``source``   — copy the mapped source column value.
  * ``const``    — always write ``value`` (HDL structural constant).
  * ``key``      — composite ``SourceSystemId``: ``prefix + sep + <key value>`` where
                   the key value is the ``key_source`` column (defaults to the
                   worker's Employee ID). ``sep`` defaults to ``"_"``.
  * ``valuemap`` — look the source value up in ``map`` (case-insensitive), else
                   ``map['default']``; ``iso_country`` maps a country name → ISO2.
  * ``date``     — reformat the source date (Excel serial or common string) → the
                   HDL ``YYYY/MM/DD`` form.
  * ``manager``  — parse a trailing numeric id out of a "Name (12345)" string and
                   prefix it (Workday manager reference → Oracle assignment number).
  * ``blank``    — required by Oracle but supplied by business later; left empty.
"""
from __future__ import annotations

# Value maps confirmed in the mapping doc's sample data.
ACTIVE_STATUS_MAP = {"active": "A", "inactive": "I", "terminated": "I", "default": "A"}
WORKER_TYPE_MAP = {
    "employee": "E", "contingent worker": "C", "contingent": "C",
    "pending worker": "P", "nonworker": "N", "default": "E",
}
# A small ISO-3166 alpha-2 crosswalk covering the countries in the NextPower
# Workday extract; unknown names fall through untouched (and a value already given
# as a 2-letter code is kept as-is by the generator).
COUNTRY_ISO2 = {
    "spain": "ES", "united states": "US", "united states of america": "US",
    "usa": "US", "india": "IN", "mexico": "MX", "canada": "CA",
    "germany": "DE", "france": "FR", "united kingdom": "GB", "uk": "GB",
    "china": "CN", "brazil": "BR", "italy": "IT", "netherlands": "NL",
    "australia": "AU", "japan": "JP", "singapore": "SG", "ireland": "IE",
    "switzerland": "CH", "sweden": "SE", "poland": "PL", "portugal": "PT",
}

# Canonical Workday source columns (the doc's "Source System Column" values). The
# real input file spells these with underscores (Employee_ID, Hire_Date); column
# matching normalises punctuation away, so one spelling covers both.
_EMP_ID = "Employee ID"
_HIRE = "Hire Date"

# The business unit every WorkTerms and Assignment row in the client's HDL template
# carries. It was _blank() here — a required Oracle attribute shipped empty.
_BU_SHORT_CODE = "NXT LLC BU"


def _src(name, source, required=True):
    """``source`` may be ONE column name or a LIST of candidate spellings.

    The client's real input file (INTXXX_CR_Oracle_Fusion_Demographic) is not the
    'Workday Extract' tab of the mapping workbook: it is 23 columns of underscores
    (``Employee_ID``, ``Job Code``, ``Manager_ID``) where the extract tab had 31 of
    spaces. Normalisation handles underscore-vs-space, but it cannot handle a
    DIFFERENT column: JobCode came from "Business Title" in the schema and the real
    file carries a proper ``Job Code`` (GSSSSM_VP2 — the exact value the client's own
    HDL template shows). A candidate list costs nothing when the first spelling is
    present and is the difference between a populated column and a blank one when it
    is not. First non-blank match wins, in the order given.
    """
    return {"name": name, "kind": "source", "source": source, "required": required}


def _const(name, value, required=True):
    return {"name": name, "kind": "const", "value": value, "required": required}


def _const_if_blank(name, value, source, required=True):
    """Constant used ONLY when the extract supplies no value for ``source``.

    Strategy 9.1 words EffectiveEndDate as "applied when no end date is present
    in the extract (open-ended)". A plain constant overwrites a real end date the
    extract does provide, which is a different rule from the one signed off.
    """
    return {"name": name, "kind": "const_if_blank", "value": value,
            "source": source, "required": required}


def _key(name, prefix, sep="_", key_source=_EMP_ID, required=True):
    return {"name": name, "kind": "key", "prefix": prefix, "sep": sep,
            "key_source": key_source, "required": required}


def _date(name, source, required=True):
    return {"name": name, "kind": "date", "source": source, "required": required}


def _vmap(name, source, mapping, required=True):
    return {"name": name, "kind": "valuemap", "source": source, "map": mapping,
            "required": required}


def _blank(name, required=True):
    return {"name": name, "kind": "blank", "required": required}


# ── Component definitions (attributes in HDL column order) ───────────────────
# Each entry: component name -> list of field specs. Worker.dat carries the Worker
# block plus its six child components (all keyed to the same employee).
LOCATION = ("Location", [
    _const("SetCode", "COMMON"),
    _vmap("ActiveStatus", "Active Status", ACTIVE_STATUS_MAP),
    # The client's own HDL template says 1990-01-01, not the "default value
    # ( 1/1/1900 )" the field-mapping workbook writes. Two documents, one digit
    # apart, and the template is the artefact they actually load — so it wins, and
    # the disagreement is recorded here rather than resolved silently. Either way
    # it must be a CONSTANT: Location is a setup object deduped to one record per
    # distinct location, so the hire date this used to carry came from whichever
    # employee row happened to survive the dedupe.
    _const("EffectiveStartDate", "1990/01/01"),
    # Strategy 9.1: open-ended ONLY when the extract has no end date. The template's
    # sample leaves this empty, which Oracle also reads as open-ended.
    _const_if_blank("EffectiveEndDate", "4712/12/31", "Location End Date",
                    required=False),
    # Template: NX_USA_CA_08 — that is Location_Code, NOT the location NAME this
    # used to copy. The real input file carries both columns.
    _src("LocationCode", ["Location Code", "Location_Code", "Location"]),
    _src("LocationName", "Location"),
    _blank("AddressLine1", required=False),
    _blank("AddressLine2", required=False),
    _blank("AddressLine3", required=False),
    _vmap("Country", "Country", {"kind": "iso_country"}),
    _blank("PostalCode", required=False),
    _blank("TownOrCity", required=False),
    _blank("Description", required=False),
])

JOB = ("Job", [
    _const("EffectiveStartDate", "1990/01/01"),
    _const("SetCode", "COMMON"),
    # Template: JobCode GSSSSM_VP2, Name "VP2, Supply Chain Management". Those are
    # the input's `Job Code` and `Job Profile` columns exactly. Both used to read
    # Business Title, which would have produced "Sr. VP, Supply Chain Management"
    # in a code column — a job code that matches nothing in Fusion.
    _src("JobCode", ["Job Code", "Job_Code", "Business Title"]),
    _src("Name", ["Job Profile", "Job_Profile", "Business Title"]),
    _vmap("ActiveStatus", "Active Status", ACTIVE_STATUS_MAP),
])

POSITION = ("Position", [
    _date("EffectiveStartDate", _HIRE),
    _blank("BusinessUnitName"),
    _vmap("ActiveStatus", "Active Status", ACTIVE_STATUS_MAP),
    _src("DepartmentName", ["Cost Center Name", "Cost_Center_Name"]),
    _blank("JobCode"),
    _const("JobSetCode", "COMMON"),
    _blank("PositionCode"),
    _src("Name", "Position"),
    # The template names these SourceSystem / SourceSystemID on Position — NOT the
    # SourceSystemOwner / SourceSystemId every other component uses. HDL matches
    # attributes by name, so the near-miss spelling is silently ignored and the
    # position loads with no source key at all.
    _const("SourceSystem", "Workday"),
    _key("SourceSystemID", "Position", key_source="Position"),
])

POSITION_HIERARCHY = ("PositionHierarchy", [
    _date("EffectiveStartDate", _HIRE),
    _const("SourceSystemOwner", "NXPHDL"),
    _key("SourceSystemId", "PosHier", key_source="Position"),
    _blank("ParentPositionCode"),
    _src("PositionCode", "Position"),
    _blank("BusinessUnitName"),
    _const("ActiveStatus", "A"),
    _blank("ParentPositionId"),
])

WORKER = ("Worker", [
    _date("EffectiveStartDate", _HIRE),
    _src("PersonNumber", _EMP_ID),
    _date("StartDate", _HIRE),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "Workday"),
    _const("ActionCode", "HIRE"),
])

PERSON_NAME = ("PersonName", [
    _date("EffectiveStartDate", _HIRE),
    _key("PersonId(SourceSystemId)", "Workday"),
    _vmap("LegislationCode", "Country", {"kind": "iso_country"}),
    _const("NameType", "GLOBAL"),
    _src("FirstName", ["Legal First Name", "Legal_First_Name"]),
    _src("LastName", ["Legal Last Name", "Legal_Last_Name"]),
    # MiddleNames was missing entirely, and KnownAs sat where it belongs. Getting an
    # HDL component's attribute LIST wrong is not cosmetic — the analyst reported
    # "the PersonName ... fields are not being reflected as expected", and this is
    # what that was.
    _src("MiddleNames", ["Legal Middle Name", "Legal_Middle_Name"], required=False),
    _src("KnownAs", ["Preferred Name", "Preferred_Name"], required=False),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "PersonName"),
])

PERSON_EMAIL = ("PersonEmail", [
    _src("PersonNumber", _EMP_ID),
    # The template carries PersonId(SourceSystemId) here and this component did not.
    # It is the link back to the Worker record — without it the e-mail has no person
    # to attach to.
    _key("PersonId(SourceSystemId)", "Workday"),
    _date("DateFrom", _HIRE),
    _const("EmailType", "W1"),
    _src("EmailAddress", "Email"),
    _const("PrimaryFlag", "Y"),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "PersonEmail"),
])

WORK_RELATIONSHIP = ("WorkRelationship", [
    _date("DateStart", _HIRE),
    # Field-mapping workbook row 54: Military_Service -> OnMilitaryServiceFlag, and
    # the template shows N where the extract is silent. The real input has the column
    # and populates it on 23 of 2,773 rows, so both halves matter: read it, and
    # default the rest to N rather than shipping an empty required flag.
    _const_if_blank("OnMilitaryServiceFlag", "N", ["Military_Service", "Military Service"]),
    _const("PrimaryFlag", "Y"),
    _key("PersonId(SourceSystemId)", "Workday"),
    _src("WorkerNumber", _EMP_ID),
    _src("LegalEmployerName", "Company"),
    _vmap("WorkerType", "Worker Type", WORKER_TYPE_MAP),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "WorkRelationship"),
    _const("ActionCode", "HIRE"),
])


# WorkTerms and Assignment appear in BOTH Worker files, with two differences the
# template states explicitly: pass one leaves AssignmentNumber and
# AssignmentStatusTypeCode empty, pass two sets them to E<employee id> and
# ACTIVE_PROCESS so the AssignmentSupervisor row has an assignment number to point
# at. Built from one definition so the two passes cannot drift apart.
def _work_terms(numbered: bool):
    return ("WorkTerms", [
        (_key("AssignmentNumber", "E", sep="") if numbered
         else _blank("AssignmentNumber")),
        (_const("AssignmentStatusTypeCode", "ACTIVE_PROCESS") if numbered
         else _blank("AssignmentStatusTypeCode")),
        _const("BusinessUnitShortCode", _BU_SHORT_CODE),
        _const("EffectiveLatestChange", "Y"),
        _const("EffectiveSequence", "1"),
        _date("EffectiveStartDate", _HIRE),
        _key("PeriodOfServiceId(SourceSystemId)", "WorkRelationship"),
        _key("PersonId(SourceSystemId)", "Workday"),
        _src("LegalEmployerName", "Company"),
        _const("SourceSystemOwner", "Workday"),
        _key("SourceSystemId", "Worker_Terms"),
        _const("ActionCode", "HIRE"),
    ])


def _assignment(numbered: bool):
    return ("Assignment", [
        _const("ActionCode", "HIRE"),
        (_key("AssignmentNumber", "E", sep="") if numbered
         else _blank("AssignmentNumber")),
        _date("EffectiveStartDate", _HIRE),
        _const("EffectiveSequence", "1"),
        _const("EffectiveLatestChange", "Y"),
        (_const("AssignmentStatusTypeCode", "ACTIVE_PROCESS") if numbered
         else _blank("AssignmentStatusTypeCode")),
        _const("BusinessUnitShortCode", _BU_SHORT_CODE),
        _key("PeriodOfServiceId(SourceSystemId)", "WorkRelationship"),
        _key("PersonId(SourceSystemId)", "Workday"),
        _vmap("WorkerType", "Worker Type", WORKER_TYPE_MAP),
        _src("LegalEmployerName", "Company"),
        _key("WorkTermsAssignmentId(SourceSystemId)", "Worker_Terms"),
        _const("SourceSystemOwner", "Workday"),
        _key("SourceSystemId", "Assignment"),
        # The template's Assignment carries DefaultExpenseAccount where this
        # component carried DepartmentName. That also settles the mapping workbook's
        # Cost_Center row, which was recorded as having no Oracle field on the
        # strength of its own TRX comment ("No suitable field is available in Oracle
        # for this information") — the client's template proves otherwise.
        _src("DefaultExpenseAccount", ["Cost Center", "Cost_Center"], required=False),
        _src("JobCode", ["Job Code", "Job_Code"], required=False),
        _src("LocationCode", ["Location Code", "Location_Code"], required=False),
    ])


WORK_TERMS = _work_terms(False)
ASSIGNMENT = _assignment(False)
WORK_TERMS_NUMBERED = _work_terms(True)
ASSIGNMENT_NUMBERED = _assignment(True)

ASSIGNMENT_SUPERVISOR = ("AssignmentSupervisor", [
    _key("AssignmentNumber", "E", sep=""),
    # Template: E111 against employee E1007802 — the manager's own assignment
    # number. The real input has a plain Manager_ID column (1001899); the older
    # "Manager - Level 01" spelling holds a "Name (12345)" string. The manager rule
    # pulls the trailing digits either way, so both spellings are listed.
    {"name": "ManagerAssignmentNumber", "kind": "manager",
     "source": ["Manager_ID", "Manager ID", "Manager - Level 01"],
     "prefix": "E", "required": True},
    _const("ManagerType", "LINE_MANAGER"),
    _date("EffectiveStartDate", _HIRE),
    _const("PrimaryFlag", "Y"),
])

# Top-level HDL objects → the .dat file each produces + its component blocks.
# ``row_scope`` = "employee" emits one record per source row (the worker itself and
# its per-employee components); "distinct" dedupes rows on ``dedup_source`` so a
# setup object (Location/Job/Position) emits one record per unique natural key.
# PositionHierarchy has no mapped source (needs parent/child build), so it is
# written METADATA-only until the client supplies the hierarchy.
#
# SIX objects, not five. The client's template splits Worker into two sheets and the
# analyst confirmed it: "Worker has two — Worker(Employee) and Worker(Assignment
# Supervisor)". It is a two-PASS load, and the reason is ordering: a supervisor row
# points at the MANAGER's assignment number, so every worker — managers included —
# has to exist before any supervisor link can resolve. Pass two re-MERGEs
# WorkRelationship, WorkTerms and Assignment with the assignment number set, then
# adds the AssignmentSupervisor block.
HDL_OBJECTS: dict[str, dict] = {
    "Location": {"dat": "Location.dat", "row_scope": "distinct",
                 "dedup_source": ["Location Code", "Location_Code", "Location"],
                 "components": [LOCATION]},
    "Job": {"dat": "Job.dat", "row_scope": "distinct",
            "dedup_source": ["Job Code", "Job_Code", "Business Title"],
            "components": [JOB]},
    "Position": {"dat": "Position.dat", "row_scope": "distinct",
                 "dedup_source": "Position", "components": [POSITION]},
    "PositionHierarchy": {"dat": "PositionHierarchy.dat", "row_scope": "none",
                          "dedup_source": None, "components": [POSITION_HIERARCHY]},
    "Worker": {"dat": "Worker.dat", "row_scope": "employee", "dedup_source": None,
               "label": "Worker(Employee)",
               "components": [WORKER, PERSON_NAME, PERSON_EMAIL, WORK_RELATIONSHIP,
                             WORK_TERMS, ASSIGNMENT]},
    "WorkerAssignmentSupervisor": {
        "dat": "Worker.dat", "row_scope": "employee", "dedup_source": None,
        "label": "Worker(AssignmentSupervisor)",
        "components": [WORK_RELATIONSHIP, WORK_TERMS_NUMBERED, ASSIGNMENT_NUMBERED,
                       ASSIGNMENT_SUPERVISOR]},
}

# The order components load in (Worker.dat internal order + across .dat files).
HDL_LOAD_ORDER = ["Location", "Job", "Position", "PositionHierarchy", "Worker",
                  "WorkerAssignmentSupervisor"]

# Business-object label used on the seeded template + conversions. Detected by the
# generator router to divert an object away from the FBDI path onto HDL.
HDL_BUSINESS_OBJECT = "Employee HDL"


def all_components() -> list[tuple[str, str, list[dict]]]:
    """(top_object, component_name, fields) for every component, in load order —
    used by the template seeder to build one FBDISheet per component.

    DEDUPED BY COMPONENT NAME. WorkRelationship, WorkTerms and Assignment each
    appear in both Worker passes, and one FBDISheet per (object, component) would
    produce two sheets called "WorkTerms" — which would split every mapping and every
    per-sheet learning scope across a pair of sheets that are the same thing. The two
    passes differ only in constants the generator supplies (the assignment number and
    ACTIVE_PROCESS), never in what an analyst maps, so the first definition wins.
    """
    out: list[tuple[str, str, list[dict]]] = []
    seen: set[str] = set()
    for obj in HDL_LOAD_ORDER:
        for comp_name, fields in HDL_OBJECTS[obj]["components"]:
            if comp_name in seen:
                continue
            seen.add(comp_name)
            out.append((obj, comp_name, fields))
    return out


def object_label(obj: str) -> str:
    """The name the client's template uses for this object, e.g. Worker(Employee)."""
    return (HDL_OBJECTS.get(obj) or {}).get("label") or obj
