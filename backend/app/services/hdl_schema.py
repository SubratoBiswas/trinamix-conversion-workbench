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

# Canonical Workday source columns (the doc's "Source System Column" values).
_EMP_ID = "Employee ID"
_HIRE = "Hire Date"


def _src(name, source, required=True):
    return {"name": name, "kind": "source", "source": source, "required": required}


def _const(name, value, required=True):
    return {"name": name, "kind": "const", "value": value, "required": required}


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
    _date("EffectiveStartDate", _HIRE),
    _const("EffectiveEndDate", "4712/12/31", required=False),
    _src("LocationCode", "Location"),
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
    _date("EffectiveStartDate", _HIRE),
    _const("SetCode", "COMMON"),
    _src("JobCode", "Business Title"),
    _src("Name", "Business Title"),
    _vmap("ActiveStatus", "Active Status", ACTIVE_STATUS_MAP),
])

POSITION = ("Position", [
    _date("EffectiveStartDate", _HIRE),
    _blank("BusinessUnitName"),
    _vmap("ActiveStatus", "Active Status", ACTIVE_STATUS_MAP),
    _src("DepartmentName", "Cost Center Name"),
    _blank("JobCode"),
    _const("JobSetCode", "COMMON"),
    _src("PositionCode", "Position"),
    _src("Name", "Position"),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "Position", key_source="Position"),
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
    _src("FirstName", "Legal First Name"),
    _src("LastName", "Legal Last Name"),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "PersonName"),
])

PERSON_EMAIL = ("PersonEmail", [
    _src("PersonNumber", _EMP_ID),
    _date("DateFrom", _HIRE),
    _const("EmailType", "W1"),
    _src("EmailAddress", "Email"),
    _const("PrimaryFlag", "Y"),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "PersonEmail"),
])

WORK_RELATIONSHIP = ("WorkRelationship", [
    _date("DateStart", _HIRE),
    _blank("OnMilitaryServiceFlag"),
    _const("PrimaryFlag", "Y"),
    _key("PersonId(SourceSystemId)", "Workday"),
    _src("WorkerNumber", _EMP_ID),
    _src("LegalEmployerName", "Company"),
    _vmap("WorkerType", "Worker Type", WORKER_TYPE_MAP),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "WorkRelationship"),
    _const("ActionCode", "HIRE"),
])

WORK_TERMS = ("WorkTerms", [
    _blank("AssignmentNumber"),
    _blank("AssignmentStatusTypeCode"),
    _blank("BusinessUnitShortCode"),
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

ASSIGNMENT = ("Assignment", [
    _const("ActionCode", "HIRE"),
    _blank("AssignmentNumber"),
    _date("EffectiveStartDate", _HIRE),
    _const("EffectiveSequence", "1"),
    _const("EffectiveLatestChange", "Y"),
    _blank("AssignmentStatusTypeCode"),
    _blank("BusinessUnitShortCode"),
    _key("PeriodOfServiceId(SourceSystemId)", "WorkRelationship"),
    _key("PersonId(SourceSystemId)", "Workday"),
    _vmap("WorkerType", "Worker Type", WORKER_TYPE_MAP),
    _src("LegalEmployerName", "Company"),
    _key("WorkTermsAssignmentId(SourceSystemId)", "Worker_Terms"),
    _const("SourceSystemOwner", "Workday"),
    _key("SourceSystemId", "Assignment"),
    _src("DepartmentName", "Cost Center Name"),
    _blank("JobCode"),
    _blank("LocationCode"),
])

ASSIGNMENT_SUPERVISOR = ("AssignmentSupervisor", [
    _key("AssignmentNumber", "E", sep=""),
    {"name": "ManagerAssignmentNumber", "kind": "manager",
     "source": "Manager - Level 01", "prefix": "E", "required": True},
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
HDL_OBJECTS: dict[str, dict] = {
    "Location": {"dat": "Location.dat", "row_scope": "distinct",
                 "dedup_source": "Location", "components": [LOCATION]},
    "Job": {"dat": "Job.dat", "row_scope": "distinct",
            "dedup_source": "Business Title", "components": [JOB]},
    "Position": {"dat": "Position.dat", "row_scope": "distinct",
                 "dedup_source": "Position", "components": [POSITION]},
    "PositionHierarchy": {"dat": "PositionHierarchy.dat", "row_scope": "none",
                          "dedup_source": None, "components": [POSITION_HIERARCHY]},
    "Worker": {"dat": "Worker.dat", "row_scope": "employee", "dedup_source": None,
               "components": [WORKER, PERSON_NAME, PERSON_EMAIL, WORK_RELATIONSHIP,
                             WORK_TERMS, ASSIGNMENT, ASSIGNMENT_SUPERVISOR]},
}

# The order components load in (Worker.dat internal order + across .dat files).
HDL_LOAD_ORDER = ["Location", "Job", "Position", "PositionHierarchy", "Worker"]

# Business-object label used on the seeded template + conversions. Detected by the
# generator router to divert an object away from the FBDI path onto HDL.
HDL_BUSINESS_OBJECT = "Employee HDL"


def all_components() -> list[tuple[str, str, list[dict]]]:
    """(top_object, component_name, fields) for every component, in load order —
    used by the template seeder to build one FBDISheet per component."""
    out: list[tuple[str, str, list[dict]]] = []
    for obj in HDL_LOAD_ORDER:
        for comp_name, fields in HDL_OBJECTS[obj]["components"]:
            out.append((obj, comp_name, fields))
    return out
