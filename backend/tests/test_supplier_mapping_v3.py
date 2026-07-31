"""The 31-Jul supplier workbook, and the column the mapping has to bind to.

Debayon Mallik, 31-Jul, with a new mapping workbook and a new NetSuite extract:

    "this morning we have got a new Mapping and Raw(input file) for supplier. now for
     mapping we must consider the Source Table Column name (the last column of the
     mapping file) as the source columns."

He is describing a specific, expensive failure. A mapping workbook is written by a
functional analyst, who names a column the way the legacy UI shows it — "1099
Eligible", "Permanent Account Number ( PAN)", "CNPJ/CPF", "Mr./Mrs...". The extract is
a database dump, and its headers are federal_reportable, pan, cnpj, prefix. Bind on
the label and the mapping row reads as MAPPED on screen while the generated FBDI ships
an empty column, because the named column is not in the file. Nothing looks wrong
anywhere. Measured against this workbook and this extract: the label binds 50 of 55
NetSuite rows, the physical name binds 55 of 55.

The five that only the physical name can reach are not minor fields —

    Federal reportable      1099 Eligible                   -> federal_reportable
    Tax Registration Number CNPJ/CPF                        -> cnpj
    Prefix                  Mr./Mrs...                      -> prefix
    Taxpayer ID             Permanent Account Number ( PAN) -> pan
    B2B Supplier Site Code  Vendor Credentials For EDI      -> vendor_credentials

— and the rule is PER ROW, not per sheet. The same workbook's 40 SyteLine rows leave
the technical column empty, because there the label already IS the physical name
(vend_num, addr##1, terms_code). Switching the whole sheet to the last column would
have dropped every one of them, which is why the fallback is part of the rule rather
than a safety net.

Two more things this edition changes, both asserted below because they alter output:

  * Supplier Name and Alternate Name SWAP. 30-Jul had Alternate Name <- Legal Name and
    Supplier Name <- Name; 31-Jul has Supplier Name <- legal_name and Alternate Name
    <- name. That is a change of meaning, not a rename, and it agrees with what the
    analyst typed into the steer box the same day.
  * A new edition supersedes the previous one IN PLACE. Seven fields are rebound, and
    a find-or-insert seeder would leave both the old and the new row live — the
    forking problem that makes "the last instruction by date wins" unexpressible,
    because one field with two live answers cannot say which is current.

And one row is deliberately NOT imported although the workbook fills it green: cnpj.
1,416 of its 1,449 values in the extract are Excel scientific notation ("3.54767E+13"),
which has rounded the CNPJ to six significant digits and cannot be recovered. The same
identifier is intact in tax_number ("35.476.658/0001-59"), which is also a green row
for the same field. Loading cnpj would ship irrecoverably wrong Brazilian tax IDs for
roughly 1,400 suppliers, so the exclusion and its reason are recorded in the data file
rather than decided silently.

Pure: stdlib + openpyxl for the synthetic workbook. No database.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_BACKEND = Path(__file__).resolve().parent.parent
_DATA = _BACKEND / "app" / "data"

_n = lambda s: re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    raise AssertionError(f"{name} {detail}".strip())


# The header row of Tracker_Netsuite_Supplier (31-Jul), verbatim. This is the ground
# truth the whole file turns on: a mapping that names a column absent from this list
# produces an empty column in the FBDI, however right it looks on screen.
EXTRACT_COLUMNS = """
federal_reportable account address_1 address_2 address_3 address_internal_id
address_phone addressee alt_email anaplan_vendor citi_bank_supplier_account cnpj
pis_cofins category city brazil_free_trade comments brazil_tax_withholding brazil_icms
country credit_limit currency dvc_last_screening_date dvc_resolution_date
dvc_resolution_status dvc_email_send_24 dvc_email_send_48 dvc_email_send_72
default_billing_address default_expense_account default_payables_account
default_shipping_address descartes_rps_alert descartes_rps_status
eligible_for_citi_finance email email_transactions enable_for_edi entity_type
external_id fax fax_transactions first_name gt_forecast_disputed hard_hit inactive
incoterm brazil_tax_reg_number brazil_municipal_tax_number intercompany_customer
intercompany_vendor internal_id is_individual last_name legal_name login_access
msme_vendor middle_name mobile prefix name cbs_ib_non_taxpayer pis_payroll
icms_st_substitute_partner parent_vendor_id pan phone primary_contact
primary_subsidiary print_as brazil_legal_name icms_tax_base federal_tax_regime
represents_subsidiary suframa business_sector soft_hit state_province
payroll_tax_relief tax_id tax_id_canada tax_number terms terms_and_conditions
vendor_approval_status vendor_credentials web_address zip_code
""".split()
EXTRACT_KEYS = {_n(c) for c in EXTRACT_COLUMNS}

_MAP_FILE = _DATA / "supplier_source_mapping_31jul.json"


def _doc():
    return json.loads(_MAP_FILE.read_text(encoding="utf-8"))


# ── 1. the shipped mapping binds to the real extract ─────────────────────────
def test_the_extract_header_list_is_the_one_we_were_given():
    check("88 columns", len(EXTRACT_COLUMNS) == 88, f"got {len(EXTRACT_COLUMNS)}")
    check("no duplicates", len(EXTRACT_KEYS) == 88)


def test_every_netsuite_mapping_names_a_column_the_extract_has():
    """The measurement. Not "most of them" — a mapping that binds to nothing is
    indistinguishable on screen from one that works."""
    ns = [m for m in _doc()["mappings"] if m.get("source_erp") == "netsuite"]
    check("there are NetSuite mappings to check", len(ns) >= 50, f"got {len(ns)}")
    miss = [m for m in ns if _n(m["source_column"]) not in EXTRACT_KEYS]
    check("every one of them binds", not miss,
          f"{len(miss)} do not: {[m['source_column'] for m in miss][:8]}")


def test_the_five_fields_the_label_could_not_reach():
    """Named, because each one is a field that would otherwise ship empty.

    Keyed as a LIST, not a single value: Taxpayer ID is fed by three green NetSuite
    columns (tax_id, pan, tax_id_canada) and asserting one of them wins would be
    asserting an insertion order nobody chose.
    """
    by: dict = {}
    for m in _doc()["mappings"]:
        by.setdefault((_n(m["target_field"]), m["source_erp"]), []).append(m)
    for field, want, label in [
        ("federalreportable", "federal_reportable", "1099 Eligible"),
        ("prefix", "prefix", "Mr./Mrs..."),
        ("taxpayerid", "pan", "Permanent Account Number"),
        ("b2bsuppliersitecode", "vendor_credentials", "Vendor Credentials For EDI"),
    ]:
        rows = by.get((field, "netsuite")) or []
        m = next((r for r in rows if r["source_column"] == want), None)
        check(f"{field} is bound to {want}", m is not None,
              f"got {[r['source_column'] for r in rows]}")
        check(f"...and the analyst's own wording is kept for {field}",
              label.lower()[:12] in (m.get("workbook_column_name") or "").lower(),
              f"got {m and m.get('workbook_column_name')!r}")


def test_syteline_rows_are_untouched_by_the_rule():
    """The half that a per-sheet reading would have destroyed. SyteLine has no
    technical column and never needed one."""
    sl = [m for m in _doc()["mappings"] if m.get("source_erp") == "syteline"]
    check("the SyteLine rows survived", len(sl) >= 38, f"got {len(sl)}")
    check("they are bound by the workbook's own column name",
          all(m.get("bound_by") == "source_column_name" for m in sl),
          f"{[m['source_column'] for m in sl if m.get('bound_by') != 'source_column_name'][:5]}")
    by = {_n(m["target_field"]): m["source_column"] for m in sl}
    check("Supplier Number still comes from vend_num", by.get("suppliernumber") == "vend_num",
          f"got {by.get('suppliernumber')!r}")
    check("Address Line 1 still comes from addr##1", by.get("addressline1") == "addr##1",
          f"got {by.get('addressline1')!r}")


def test_supplier_name_and_alternate_name_swapped():
    """A change of meaning, so it is asserted rather than assumed. It also matches the
    instruction the analyst typed into the steer box the same day."""
    by = {(_n(m["target_field"]), m["source_erp"]): m["source_column"]
          for m in _doc()["mappings"]}
    check("Supplier Name <- legal_name", by.get(("suppliername", "netsuite")) == "legal_name",
          f"got {by.get(('suppliername', 'netsuite'))!r}")
    check("Alternate Name <- name", by.get(("alternatename", "netsuite")) == "name",
          f"got {by.get(('alternatename', 'netsuite'))!r}")


def test_the_corrupted_cnpj_column_is_excluded_with_its_reason():
    """Excluding a row the workbook marked green is a decision, so it is written down
    where the seeder returns it, not buried."""
    d = _doc()
    excl = d.get("_excluded_with_reason") or []
    cn = [e for e in excl if _n(e.get("source_column")) == "cnpj"]
    check("cnpj is excluded", cn, "it is still being seeded")
    check("with a reason that names the corruption",
          "scientific notation" in (cn[0].get("reason") or "").lower())
    check("and it is not in the mappings",
          all(_n(m["source_column"]) != "cnpj" for m in d["mappings"]))
    check("while tax_number, which holds the same identifier intact, IS mapped",
          any(m["source_column"] == "tax_number"
              and _n(m["target_field"]) == "taxregistrationnumber" for m in d["mappings"]),
          "Tax Registration Number now has no source at all")


def test_the_row_the_workbook_contradicts_itself_on_is_not_guessed():
    """Green fill but Bring to Oracle = no, on Supplier Number — the supplier key.
    The colour is a legend and the column is an instruction; guessing either way
    would be worse than saying so."""
    d = _doc()
    ex = [e for e in (d.get("_excluded_with_reason") or [])
          if _n(e.get("target_field")) == "suppliernumber"]
    check("it is excluded and explained", ex, "a Supplier Number mapping was guessed")
    check("the disagreement with the extract is recorded too",
          "internal_id" in (ex[0].get("reason") or ""),
          "the workbook says no data exists; the extract has it 100% populated")


def test_the_edition_declares_what_it_supersedes():
    """Without this the seeder cannot rewrite the previous edition's rows and every
    rebound field ends up with two live answers."""
    d = _doc()
    check("it has a stable label", (d.get("_label") or "").strip())
    check("it names its predecessor", (d.get("_supersedes") or "").strip())
    old = json.loads((_DATA / "supplier_source_mapping_30jul.json").read_text(encoding="utf-8"))
    check("the predecessor label is the one the old rows were written under",
          d["_supersedes"] == "NXT Supplier Mapping 30Jul26 (green rows)",
          f"got {d['_supersedes']!r}")
    check("and the new edition is dated later",
          d["_effective_date"] > old["_effective_date"],
          f"{d['_effective_date']} vs {old['_effective_date']}")


# ── 2. the ingest, driven end to end over a real workbook ────────────────────
def _workbook(path):
    """A miniature of the real layout: label column, technical column last, and a
    SyteLine block whose technical cell is empty."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Legend", None, None, None, None, None])          # junk row, as in the real file
    ws.append(["Source System", "Source Column Name", "Bring to Oracle",
               "Oracle Field Name", "TRX Comments", "Source Table Column name"])
    for row in [
        ["Netsuite", "1099 Eligible", "Yes", "Federal reportable", "", "federal_reportable"],
        ["Netsuite", "Legal Name", "Yes", "Supplier Name", "", "legal_name"],
        ["Netsuite", "Mr./Mrs...", "Yes", "Prefix", "", "prefix"],
        ["SyteLine", "vend_num", "Yes", "Supplier Number", "", None],
        ["SyteLine", "addr##1", "Yes", "Address Line 1", "", None],
    ]:
        ws.append(row)
    wb.save(path)


def _ingest(path):
    """Drive the REAL analyze_mapping_file, with only the database stubbed out.

    MappingProposal is a Beanie Document and refuses to be constructed without an
    initialised collection, so it is swapped for a plain pydantic model carrying the
    same fields. Everything the test asserts on — header detection, the per-row
    source choice, the layout note — is the shipped code.
    """
    from typing import Any, Optional

    from pydantic import BaseModel, Field

    from app.models.mapping_proposal import ProposedMapping
    from app.services import mapping_ingest_service as mis

    class _Proposal(BaseModel):
        file_name: str = ""
        client_id: Any = None
        target_object: Optional[str] = None
        source_system: Optional[str] = None
        uploaded_by: str = ""
        count_skipped: int = 0
        detected_columns: dict = Field(default_factory=dict)
        detected_source_systems: dict = Field(default_factory=dict)
        rows: list[ProposedMapping] = Field(default_factory=list)
        layout_method: str = ""
        layout_note: str = ""
        source_systems: list[str] = Field(default_factory=list)
        source_system_conflict: Optional[str] = None

        async def insert(self):
            return self

    async def _noop_classify(_p):
        return None

    old_p, old_c = mis.MappingProposal, mis._classify
    mis.MappingProposal, mis._classify = _Proposal, _noop_classify
    try:
        return asyncio.run(mis.analyze_mapping_file(
            path, file_name="wb.xlsx", default_object="Supplier"))
    finally:
        mis.MappingProposal, mis._classify = old_p, old_c


def test_the_ingest_picks_the_physical_column_and_falls_back_per_row():
    """The generic half of the fix: any workbook laid out like this one, uploaded by
    anyone, binds the same way — the seeded NextPower data is only today's instance."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "wb.xlsx")
        _workbook(p)
        prop = _ingest(p)
    got = {_n(r.target_field): r.source_field for r in prop.rows}
    check("the label row bound to the physical name",
          got.get("federalreportable") == "federal_reportable",
          f"got {got.get('federalreportable')!r}")
    check("Supplier Name bound to legal_name", got.get("suppliername") == "legal_name",
          f"got {got.get('suppliername')!r}")
    check("Prefix bound to prefix", got.get("prefix") == "prefix")
    check("the SyteLine row with no physical cell kept its own name",
          got.get("suppliernumber") == "vend_num", f"got {got.get('suppliernumber')!r}")
    check("...and so did the second one", got.get("addressline1") == "addr##1",
          f"got {got.get('addressline1')!r}")
    check("nothing was dropped", len(prop.rows) == 5, f"got {len(prop.rows)}")
    raw = {_n(r.target_field): r.source_raw for r in prop.rows}
    check("the analyst's wording is carried alongside",
          raw.get("federalreportable") == "1099 Eligible",
          f"got {raw.get('federalreportable')!r}")
    check("and the layout note says the physical column was used",
          "physical column name" in (prop.layout_note or ""),
          f"got {prop.layout_note!r}")


def test_a_workbook_without_a_physical_column_is_unchanged():
    """The regression guard. Every mapping document uploaded before today has only
    the one source column, and must keep binding to it."""
    import openpyxl
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "wb.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Source System", "Source Column Name", "Oracle Field Name"])
        ws.append(["Netsuite", "Legal Name", "Supplier Name"])
        ws.append(["Netsuite", "Address 1", "Address Line 1"])
        wb.save(p)
        prop = _ingest(p)
    got = {_n(r.target_field): r.source_field for r in prop.rows}
    check("it still binds to the only column there is",
          got.get("suppliername") == "Legal Name", f"got {got.get('suppliername')!r}")
    check("both rows survived", len(prop.rows) == 2, f"got {len(prop.rows)}")


def test_the_physical_column_is_not_confused_with_the_source_system_column():
    """`_find` scores by longest alias, and 'Source System' / 'Source Table Column
    name' / 'Source Column Name' all begin the same way. Picking the wrong one here
    imports every row bound to the word "Netsuite"."""
    from app.services.mapping_ingest_service import _SRC, _SRC_PHYSICAL, _find
    headers = ["Source System", "Source File", "Source Column Name", "Description",
               "Bring to Oracle", "Duplicate Of", "Oracle Field Name",
               "Transactional/Flexfield Page", "NXT IT Feedback", "Business Feedback",
               "TRX Comments", "Source Table Column name"]
    check("the label column is index 2", _find(headers, _SRC) == 2,
          f"got {_find(headers, _SRC)} ({headers[_find(headers, _SRC)]!r})")
    check("the physical column is the last one", _find(headers, _SRC_PHYSICAL) == 11,
          f"got {_find(headers, _SRC_PHYSICAL)}")


def _all():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    for name, fn in _all():
        print(name)
        fn()
    print(f"\n{len(_all())} tests passed")
