"""NextPower Employee Conversion Strategy (Workday -> Fusion HCM) v1.0.

Checks the loader schema against the SIGNED specification, so a later edit that
contradicts the document fails here rather than in a load file.

Covers section 9 defaulting rules, the section 11 load sequence, and the two
changes this audit produced: EffectiveEndDate is conditional, not a constant
(9.1 says "applied when no end date is present"), and the render path for it.

Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hdl_output_service import render_cell  # noqa: E402
from app.services.hdl_schema import (  # noqa: E402
    ASSIGNMENT, ASSIGNMENT_SUPERVISOR, HDL_LOAD_ORDER, HDL_OBJECTS, JOB,
    LOCATION, PERSON_EMAIL, PERSON_NAME, WORK_RELATIONSHIP, WORK_TERMS, WORKER,
)

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def spec(component, field_name):
    _, fields = component
    for f in fields:
        if f["name"] == field_name:
            return f
    return None


def const_of(component, field_name):
    f = spec(component, field_name)
    return f.get("value") if f else None


def key_of(component, field_name):
    f = spec(component, field_name)
    if not f or f.get("kind") != "key":
        return None
    return f"{f.get('prefix','')}{f.get('sep','_')}<{f.get('key_source')}>"


# ── 9.1 Location / 9.2 Job ───────────────────────────────────────────────────
def test_location_setcode_and_open_ended_end_date():
    check("SetCode COMMON", const_of(LOCATION, "SetCode") == "COMMON")
    f = spec(LOCATION, "EffectiveEndDate")
    check("open-ended value", f["value"] == "4712/12/31", f"got {f}")
    # 9.1 says "applied when no end date is PRESENT in the extract". A plain
    # constant would overwrite a real end date the extract supplies.
    check("conditional, not a constant", f["kind"] == "const_if_blank",
          f"got {f['kind']}")


def test_effective_end_date_yields_to_the_extract():
    f = spec(LOCATION, "EffectiveEndDate")
    check("extract value wins",
          render_cell(f, lambda n, s: "2030/06/30") == "2030/06/30")
    check("blank falls back to open-ended",
          render_cell(f, lambda n, s: "") == "4712/12/31")
    check("missing column falls back too",
          render_cell(f, lambda n, s: None) == "4712/12/31")


def test_job_setcode():
    check("SetCode COMMON", const_of(JOB, "SetCode") == "COMMON")


# ── 9.5 Worker family constants ──────────────────────────────────────────────
def test_worker_defaults():
    check("SourceSystemOwner Workday", const_of(WORKER, "SourceSystemOwner") == "Workday")
    check("ActionCode HIRE", const_of(WORKER, "ActionCode") == "HIRE")
    check("SourceSystemId Workday_<Employee ID>",
          key_of(WORKER, "SourceSystemId") == "Workday_<Employee ID>",
          f"got {key_of(WORKER, 'SourceSystemId')}")


def test_person_name_defaults():
    check("NameType GLOBAL", const_of(PERSON_NAME, "NameType") == "GLOBAL")
    check("SourceSystemOwner Workday",
          const_of(PERSON_NAME, "SourceSystemOwner") == "Workday")
    check("SourceSystemId PersonName_<Employee ID>",
          key_of(PERSON_NAME, "SourceSystemId") == "PersonName_<Employee ID>")
    check("PersonId cross-references the Worker key",
          key_of(PERSON_NAME, "PersonId(SourceSystemId)") == "Workday_<Employee ID>")


def test_person_email_defaults():
    check("EmailType W1", const_of(PERSON_EMAIL, "EmailType") == "W1")
    check("PrimaryFlag Y", const_of(PERSON_EMAIL, "PrimaryFlag") == "Y")
    check("SourceSystemOwner Workday",
          const_of(PERSON_EMAIL, "SourceSystemOwner") == "Workday")
    check("SourceSystemId PersonEmail_<Employee ID>",
          key_of(PERSON_EMAIL, "SourceSystemId") == "PersonEmail_<Employee ID>")


def test_work_relationship_defaults():
    check("PrimaryFlag Y", const_of(WORK_RELATIONSHIP, "PrimaryFlag") == "Y")
    check("ActionCode HIRE", const_of(WORK_RELATIONSHIP, "ActionCode") == "HIRE")
    check("SourceSystemId WorkRelationship_<Employee ID>",
          key_of(WORK_RELATIONSHIP, "SourceSystemId") == "WorkRelationship_<Employee ID>")
    check("PersonId cross-references the Worker key",
          key_of(WORK_RELATIONSHIP, "PersonId(SourceSystemId)") == "Workday_<Employee ID>")
    # "Not sourced from Workday — default value pending Business confirmation."
    # Blank is the honest representation of an undecided default.
    check("OnMilitaryServiceFlag left blank pending confirmation",
          spec(WORK_RELATIONSHIP, "OnMilitaryServiceFlag")["kind"] == "blank")


def test_work_terms_defaults():
    check("EffectiveLatestChange Y", const_of(WORK_TERMS, "EffectiveLatestChange") == "Y")
    check("EffectiveSequence 1", const_of(WORK_TERMS, "EffectiveSequence") == "1")
    check("ActionCode HIRE", const_of(WORK_TERMS, "ActionCode") == "HIRE")
    check("SourceSystemId Worker_Terms_<Employee ID>",
          key_of(WORK_TERMS, "SourceSystemId") == "Worker_Terms_<Employee ID>")
    check("PeriodOfServiceId cross-references WorkRelationship",
          key_of(WORK_TERMS, "PeriodOfServiceId(SourceSystemId)")
          == "WorkRelationship_<Employee ID>")
    # OI-02: BusinessUnitShortCode is an open item, so it must ship blank rather
    # than carry a guessed value.
    check("BusinessUnitShortCode blank (OI-02)",
          spec(WORK_TERMS, "BusinessUnitShortCode")["kind"] == "blank")


def test_assignment_defaults_and_cross_references():
    check("ActionCode HIRE", const_of(ASSIGNMENT, "ActionCode") == "HIRE")
    check("EffectiveSequence 1", const_of(ASSIGNMENT, "EffectiveSequence") == "1")
    check("EffectiveLatestChange Y", const_of(ASSIGNMENT, "EffectiveLatestChange") == "Y")
    check("SourceSystemId Assignment_<Employee ID>",
          key_of(ASSIGNMENT, "SourceSystemId") == "Assignment_<Employee ID>")
    check("WorkTermsAssignmentId cross-references WorkTerms",
          key_of(ASSIGNMENT, "WorkTermsAssignmentId(SourceSystemId)")
          == "Worker_Terms_<Employee ID>")
    check("PeriodOfServiceId cross-references WorkRelationship",
          key_of(ASSIGNMENT, "PeriodOfServiceId(SourceSystemId)")
          == "WorkRelationship_<Employee ID>")


def test_assignment_supervisor_defaults():
    check("ManagerType LINE_MANAGER",
          const_of(ASSIGNMENT_SUPERVISOR, "ManagerType") == "LINE_MANAGER")
    check("PrimaryFlag Y", const_of(ASSIGNMENT_SUPERVISOR, "PrimaryFlag") == "Y")
    f = spec(ASSIGNMENT_SUPERVISOR, "AssignmentNumber")
    check("AssignmentNumber is E + Employee ID with no separator",
          f["prefix"] == "E" and f["sep"] == "", f"got {f}")


def test_manager_number_is_parsed_from_name_id_format():
    """9.5: parsed from the Workday "Manager - Level 01" field, "Name (ID)"."""
    f = spec(ASSIGNMENT_SUPERVISOR, "ManagerAssignmentNumber")
    check("E + the id inside the parentheses",
          render_cell(f, lambda n, s: "Jane Smith (1001898)") == "E1001898")
    check("blank when there is no id",
          render_cell(f, lambda n, s: "Unknown") == "")


def test_assignment_number_render():
    f = spec(ASSIGNMENT_SUPERVISOR, "AssignmentNumber")
    check("E1001898", render_cell(f, lambda n, s: "1001898") == "E1001898")
    check("blank when no employee id", render_cell(f, lambda n, s: "") == "")


# ── Section 11: load sequence ────────────────────────────────────────────────
def test_load_order_matches_the_document():
    """Each object depends on records the previous one created, so the order is
    part of the specification, not a preference."""
    check("Location first", HDL_LOAD_ORDER[0] == "Location")
    check("Job second", HDL_LOAD_ORDER[1] == "Job")
    check("Worker before its children", "Worker" in HDL_LOAD_ORDER)
    worker_components = [c for c, _ in HDL_OBJECTS["Worker"]["components"]]
    check("Worker.dat orders its components as the document lists them",
          worker_components == ["Worker", "PersonName", "PersonEmail",
                                "WorkRelationship", "WorkTerms", "Assignment",
                                "AssignmentSupervisor"],
          f"got {worker_components}")


def test_every_load_object_names_its_dat_file():
    for obj in HDL_LOAD_ORDER:
        check(f"{obj} -> {obj}.dat",
              HDL_OBJECTS[obj]["dat"] == f"{obj}.dat",
              f"got {HDL_OBJECTS[obj]['dat']}")


def test_setup_objects_are_deduplicated():
    """Location and Job are setup data — one record per unique natural key, not
    one per employee."""
    for obj in ("Location", "Job"):
        check(f"{obj} is distinct-scoped",
              HDL_OBJECTS[obj]["row_scope"] == "distinct")
    check("Worker is per-employee",
          HDL_OBJECTS["Worker"]["row_scope"] == "employee")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
