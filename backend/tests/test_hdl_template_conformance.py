"""The client's HDL template is the FORMAT the download must take — not a gold record.

Analyst, 31-Jul, drawing the line: "treat template as purely a format in which FBDI
downloads should happen, the inputs are for reference, check the mappings file for
mapping and perform the mapping."

So this file asserts STRUCTURE and nothing else:

  * every component the template shows exists, carrying at least the attributes it
    shows, in the order it shows them;
  * the two-pass Worker split, and the assignment number that only pass two sets;
  * that each target field reads the SOURCE COLUMN THE MAPPING FILE NAMES.

It deliberately does NOT assert the template's sample VALUES. Those are one
hand-written row for one employee and several are illustrative rather than expected:
the company reads "Nextpower LLC" where the real input says "Nextracker LLC, USA",
and DefaultExpenseAccount shows a full accounting flexfield where the mapped column
holds a cost centre. Pinning those would turn a reference into a contract and fail
the next time a legitimate value changed. Extra attributes are allowed for the same
reason — the template is a floor, not a ceiling.

CW_Issues 31-Jul, row 28: "While testing them in our tool, we noticed that some
fields are not being reflected as expected. For example, the PersonName,
AssignmentSupervisor, etc fields."

Three documents came with that: HDL Template 3.xlsx, the mapping workbook, and the
actual input INTXXX_CR_Oracle_Fusion_Demographic_V2. Generating employee 1007802 from
the real input and comparing the SHAPE against the template found seven structural
gaps, none of which any existing test was looking for:

  PersonName      MiddleNames was MISSING and KnownAs sat in its place. Getting a
                  component's attribute LIST wrong is not cosmetic; this IS
                  "the PersonName fields are not being reflected".
  PersonEmail     PersonId(SourceSystemId) was missing — the link back to the
                  Worker record, without which the e-mail has no person.
  Assignment      DefaultExpenseAccount, which the mapping workbook's own TRX
                  comment says does not exist in Oracle ("No suitable field is
                  available in Oracle for this information"). It does. Deferring to
                  a note ABOUT the data instead of reading the artefact is how a
                  green row got written off.
  Assignment      JobCode and LocationCode shipped blank; the template populates
                  both, and the input has the columns.
  Job             JobCode read "Business Title" (Sr. VP, Supply Chain Management)
                  where the template shows GSSSSM_VP2 — which is the input's
                  `Job Code` column, exactly.
  WorkTerms       BusinessUnitShortCode was blank as an open item (OI-02). The
                  template answers it: NXT LLC BU.
  Position        SourceSystem / SourceSystemID, not SourceSystemOwner /
                  SourceSystemId. HDL matches attributes by name, so a near-miss
                  spelling is ignored in silence.

And the sixth load object. The analyst: "Worker has two — Worker(Employee) and
Worker(Assignment Supervisor)". It is a two-PASS load: a supervisor row points at the
MANAGER's assignment number, so every worker — managers included — has to exist
before any of those links resolve. Pass two re-MERGEs the assignment chain with
AssignmentNumber = E<employee id> and ACTIVE_PROCESS set, then adds the supervisor
block. Pass one leaves both empty, which is why the supervisor rows referenced an
assignment number no assignment row carried.

Pure: stdlib + the schema and the render path. The template's expectations are
vendored below, so this runs without the workbook.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hdl_output_service import _norm, render_cell        # noqa: E402
from app.services.hdl_schema import HDL_LOAD_ORDER, HDL_OBJECTS       # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── The client's template: attribute lists, verbatim, in order ──────────────
TEMPLATE_ATTRS = {
    "Location": ["SetCode", "ActiveStatus", "EffectiveStartDate", "EffectiveEndDate",
                 "LocationCode", "LocationName", "AddressLine1", "AddressLine2",
                 "AddressLine3", "Country", "PostalCode", "TownOrCity", "Description"],
    "Job": ["EffectiveStartDate", "SetCode", "JobCode", "Name", "ActiveStatus"],
    "Position": ["EffectiveStartDate", "BusinessUnitName", "ActiveStatus",
                 "DepartmentName", "JobCode", "JobSetCode", "PositionCode", "Name",
                 "SourceSystem", "SourceSystemID"],
    "PositionHierarchy": ["EffectiveStartDate", "SourceSystemOwner", "SourceSystemId",
                          "ParentPositionCode", "PositionCode", "BusinessUnitName",
                          "ActiveStatus", "ParentPositionId"],
    "Worker": ["EffectiveStartDate", "PersonNumber", "StartDate", "SourceSystemOwner",
               "SourceSystemId", "ActionCode"],
    "PersonName": ["EffectiveStartDate", "PersonId(SourceSystemId)", "LegislationCode",
                   "NameType", "FirstName", "LastName", "MiddleNames", "KnownAs",
                   "SourceSystemOwner", "SourceSystemId"],
    "PersonEmail": ["PersonNumber", "PersonId(SourceSystemId)", "DateFrom", "EmailType",
                    "EmailAddress", "PrimaryFlag", "SourceSystemOwner", "SourceSystemId"],
    "WorkRelationship": ["DateStart", "OnMilitaryServiceFlag", "PrimaryFlag",
                         "PersonId(SourceSystemId)", "WorkerNumber", "LegalEmployerName",
                         "WorkerType", "SourceSystemOwner", "SourceSystemId", "ActionCode"],
    "WorkTerms": ["AssignmentNumber", "AssignmentStatusTypeCode", "BusinessUnitShortCode",
                  "EffectiveLatestChange", "EffectiveSequence", "EffectiveStartDate",
                  "PeriodOfServiceId(SourceSystemId)", "PersonId(SourceSystemId)",
                  "LegalEmployerName", "SourceSystemOwner", "SourceSystemId", "ActionCode"],
    "Assignment": ["ActionCode", "AssignmentNumber", "EffectiveStartDate",
                   "EffectiveSequence", "EffectiveLatestChange", "AssignmentStatusTypeCode",
                   "BusinessUnitShortCode", "PeriodOfServiceId(SourceSystemId)",
                   "PersonId(SourceSystemId)", "WorkerType", "LegalEmployerName",
                   "WorkTermsAssignmentId(SourceSystemId)", "SourceSystemOwner",
                   "SourceSystemId", "DefaultExpenseAccount", "JobCode", "LocationCode"],
    "AssignmentSupervisor": ["AssignmentNumber", "ManagerAssignmentNumber", "ManagerType",
                             "EffectiveStartDate", "PrimaryFlag"],
}

# Employee 1007802 as the REAL input file holds him (row 6, header on row 2).
ROW = {
    "Employee_ID": "1007802", "Preferred_Name": "Yves Figuerola",
    "Legal_First_Name": "Yves", "Legal_Last_Name": "Figuerola",
    "Company": "Nextracker LLC, USA", "Hire_Date": "2021-01-01 00:00:00",
    "Worker_Type": "Employee", "Active_Status": "Yes", "Manager_ID": "944886",
    "Location": "Fremont, South Bldg, California - USA", "Location_Code": "NX_USA_CA_08",
    "Country": "United States of America", "Cost_Center": "200",
    "Cost_Center_Name": "Operations", "Business_Title": "Sr. VP, Supply Chain Management",
    "Job Profile": "VP2, Supply Chain Management", "Position": "Sr. Vice President",
    "Position_ID": "P-1007802", "Job Code": "GSSSSM_VP2",
    "Email": "yfiguerola@nextpower.com", "Military_Service": "",
}
_BY_NORM = {_norm(k): k for k in ROW}


def resolve(field_name, source):
    for cand in (source if isinstance(source, (list, tuple)) else [source]):
        if cand and _norm(cand) in _BY_NORM:
            return ROW[_BY_NORM[_norm(cand)]]
    return None


def component(obj, name):
    for cn, fields in HDL_OBJECTS[obj]["components"]:
        if cn == name:
            return fields
    raise AssertionError(f"{obj} has no {name} component")


def rendered(obj, name):
    fields = component(obj, name)
    return dict(zip([f["name"] for f in fields],
                    [render_cell(f, resolve) for f in fields]))


def test_every_component_covers_the_templates_attributes_in_its_order():
    """The template is the FORMAT: every attribute it lists must be present, and in
    the relative order it lists them, because HDL reads METADATA positionally against
    the names on that line. Extra attributes are allowed — the template is a floor,
    not a ceiling, and a later Oracle requirement should not fail here."""
    seen = {}
    for obj in HDL_LOAD_ORDER:
        for cn, fields in HDL_OBJECTS[obj]["components"]:
            seen.setdefault(cn, [f["name"] for f in fields])
    for cn, want in TEMPLATE_ATTRS.items():
        check(f"{cn} exists", cn in seen, f"components are {sorted(seen)}")
        got = seen[cn]
        missing = [a for a in want if a not in got]
        check(f"{cn} carries every attribute the template shows", not missing,
              f"missing {missing}")
        positions = [got.index(a) for a in want]
        check(f"{cn} keeps the template's relative order",
              positions == sorted(positions),
              f"\n      got  {got}\n      want {want} in order")


def test_the_two_worker_passes_are_the_templates_two_sheets():
    check("six objects", len(HDL_LOAD_ORDER) == 6, f"got {HDL_LOAD_ORDER}")
    emp = [c for c, _ in HDL_OBJECTS["Worker"]["components"]]
    sup = [c for c, _ in HDL_OBJECTS["WorkerAssignmentSupervisor"]["components"]]
    check("Worker(Employee) is the template's first sheet",
          emp == ["Worker", "PersonName", "PersonEmail", "WorkRelationship",
                  "WorkTerms", "Assignment"], f"got {emp}")
    check("Worker(AssignmentSupervisor) is its second",
          sup == ["WorkRelationship", "WorkTerms", "Assignment",
                  "AssignmentSupervisor"], f"got {sup}")


def test_the_assignment_number_exists_in_pass_two_and_not_in_pass_one():
    """The whole reason there are two passes. The supervisor row points at
    E1007802, so the WorkTerms and Assignment rows loaded alongside it must carry
    that number — while pass one leaves it to Oracle."""
    one_wt = rendered("Worker", "WorkTerms")
    two_wt = rendered("WorkerAssignmentSupervisor", "WorkTerms")
    two_as = rendered("WorkerAssignmentSupervisor", "AssignmentSupervisor")
    check("pass one leaves the number to Oracle", one_wt["AssignmentNumber"] == "")
    check("pass one leaves the status too", one_wt["AssignmentStatusTypeCode"] == "")
    check("pass two sets it", two_wt["AssignmentNumber"] == "E1007802",
          f"got {two_wt['AssignmentNumber']!r}")
    check("pass two sets ACTIVE_PROCESS",
          two_wt["AssignmentStatusTypeCode"] == "ACTIVE_PROCESS")
    check("and the supervisor row points at the same number",
          two_as["AssignmentNumber"] == two_wt["AssignmentNumber"])
    check("at the MANAGER's number, from Manager_ID",
          two_as["ManagerAssignmentNumber"] == "E944886",
          f"got {two_as['ManagerAssignmentNumber']!r}")


def test_each_field_reads_the_column_the_mapping_file_names():
    """Values, checked against the MAPPING FILE rather than the template's sample row.
    The mapping file is the authority for which source column feeds which target
    field; the template's row is one hand-written example and several of its values
    are illustrative."""
    w = rendered("Worker", "Worker")
    check("PersonNumber", w["PersonNumber"] == "1007802")
    check("SourceSystemId", w["SourceSystemId"] == "Workday_1007802")
    check("StartDate", w["StartDate"] == "2021/01/01", f"got {w['StartDate']}")

    pn = rendered("Worker", "PersonName")
    check("LegislationCode US", pn["LegislationCode"] == "US")
    check("FirstName", pn["FirstName"] == "Yves")
    check("LastName", pn["LastName"] == "Figuerola")
    check("MiddleNames empty here but PRESENT", pn["MiddleNames"] == "")
    check("KnownAs", pn["KnownAs"] == "Yves Figuerola")
    check("SourceSystemId", pn["SourceSystemId"] == "PersonName_1007802")

    pe = rendered("Worker", "PersonEmail")
    check("EmailAddress", pe["EmailAddress"] == "yfiguerola@nextpower.com")
    check("PersonId links back to the Worker",
          pe["PersonId(SourceSystemId)"] == "Workday_1007802")

    wr = rendered("Worker", "WorkRelationship")
    check("WorkerType E", wr["WorkerType"] == "E")
    check("OnMilitaryServiceFlag N", wr["OnMilitaryServiceFlag"] == "N")
    check("WorkerNumber", wr["WorkerNumber"] == "1007802")

    a = rendered("Worker", "Assignment")
    check("BusinessUnitShortCode", a["BusinessUnitShortCode"] == "NXT LLC BU")
    check("JobCode from the input's Job Code", a["JobCode"] == "GSSSSM_VP2",
          f"got {a['JobCode']!r}")
    check("LocationCode", a["LocationCode"] == "NX_USA_CA_08")
    check("WorkTermsAssignmentId", a["WorkTermsAssignmentId(SourceSystemId)"]
          == "Worker_Terms_1007802")

    loc = rendered("Location", "Location")
    check("LocationCode is the CODE, not the name",
          loc["LocationCode"] == "NX_USA_CA_08", f"got {loc['LocationCode']!r}")
    check("LocationName is the name",
          loc["LocationName"] == "Fremont, South Bldg, California - USA")
    check("Country as ISO2", loc["Country"] == "US")

    job = rendered("Job", "Job")
    check("Job.JobCode", job["JobCode"] == "GSSSSM_VP2", f"got {job['JobCode']!r}")
    check("Job.Name from Job Profile",
          job["Name"] == "VP2, Supply Chain Management", f"got {job['Name']!r}")


def test_the_default_expense_account_carries_the_mapped_column():
    """The mapping workbook's row 39 says Cost_Center -> DefaultExpenseAccount, and
    the workbook's own TRX comment saying no Oracle field exists is contradicted by
    the template, which carries the attribute on Assignment. So: the attribute exists
    (format, from the template) and it reads Cost_Center (mapping, from the mapping
    file). The template's own cell shows a full accounting flexfield string — that is
    a sample, not a transformation this file invents."""
    a = rendered("Worker", "Assignment")
    check("reads the mapped column verbatim", a["DefaultExpenseAccount"] == "200",
          f"got {a['DefaultExpenseAccount']!r}")


def test_a_candidate_source_list_survives_either_spelling():
    """The mapping workbook, the extract tab and the real input spell the same
    column three ways. A single guessed spelling binds to nothing, silently."""
    fields = {f["name"]: f for f in component("Job", "Job")}
    src = fields["JobCode"]["source"]
    check("JobCode lists candidates", isinstance(src, (list, tuple)))
    check("the real input's spelling is first", src[0] == "Job Code", f"got {src}")
    check("with the old canonical source kept as a fallback",
          "Business Title" in src)
    # Resolution must not depend on which spelling the file happens to use.
    for spelling in ("Job Code", "Job_Code"):
        row = {spelling: "GSSSSM_VP2"}
        by = {_norm(k): k for k in row}
        got = render_cell(src and fields["JobCode"],
                          lambda n, s: next((row[by[_norm(c)]] for c in
                                             (s if isinstance(s, (list, tuple)) else [s])
                                             if _norm(c) in by), None))
        check(f"{spelling} resolves", got == "GSSSSM_VP2", f"got {got!r}")


def test_position_uses_the_templates_spelling_of_the_source_system_keys():
    """SourceSystem / SourceSystemID on Position, not the SourceSystemOwner /
    SourceSystemId every other component uses. HDL matches by name, so the
    near-miss is ignored in silence and the position loads with no source key."""
    names = [f["name"] for f in component("Position", "Position")]
    check("SourceSystem", "SourceSystem" in names)
    check("SourceSystemID", "SourceSystemID" in names)
    check("and not the other spelling",
          "SourceSystemOwner" not in names and "SourceSystemId" not in names)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nall HDL template conformance checks passed")
