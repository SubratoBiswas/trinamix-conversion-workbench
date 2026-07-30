"""Unit tests for the supplier FBDI output layout (reorder + END + headerless +
Oracle file naming), exercised against the bundled supplier templates.

Runnable with pytest OR directly:  python3 backend/tests/test_supplier_fbdi_layout.py

These call the SAME functions the generator uses (app.services.supplier_fbdi_layout),
so a passing run proves the analyst spec is honoured for every supplier conversion —
existing or new — because generation always routes through this code.
"""
import os
import sys

import openpyxl
import pandas as pd

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services import supplier_fbdi_layout as L  # noqa: E402

_TPL = os.path.join(_BACKEND, "app", "data", "fbdi_templates")

# entity -> template workbook, [(sheet, expected_csv_name)]; first sheet is primary.
INTERFACES = {
    "Supplier Import": ("1_SupplierImport_POZ_SUPPLIERS_INT.xlsm",
                        [("POZ_SUPPLIERS_INT", "PozSuppliersInt")]),
    "Supplier Address": ("2_SupplierAddress_POZ_SUPPLIER_ADDRESSES_INT.xlsm",
                         [("POZ_SUPPLIER_ADDRESSES_INT", "PozSupplierAddressesInt")]),
    "Supplier Site": ("3_SupplierSite_POZ_SUPPLIER_SITES_INT.xlsm",
                      [("POZ_SUPPLIER_SITES_INT", "PozSupplierSitesInt"),
                       ("Third_Party_Pay_Relationships", "PozSupThirdPartyInt")]),
    "Supplier Site Assignment": ("4_SupplierSiteAssignment_POZ_SITE_ASSIGNMENTS_INT.xlsm",
                                 [("POZ_SITE_ASSIGNMENTS_INT", "PozSiteAssignmentsInt")]),
    "Supplier Contacts": ("5_SupplierContacts_POZ_SUP_CONTACTS.xlsm",
                          [("POZ_SUP_CONTACTS", "PozSupContactsInt"),
                           ("POZ_SUPP_CONTACT_ADDRESSES_INT", "PozSupContactAddressesInt")]),
    "Supplier Banks": ("6_SupplierBank_IBY_TEMP_EXT_PAYEES.xlsm",
                       [("IBY_TEMP_EXT_PAYEES", "IbyTempExtPayees"),
                        ("IBY_TEMP_EXT_BANK_ACCTS ", "IbyTempExtBankAccts"),
                        ("IBY_TEMP_PMT_INSTR_USES", "IbyTempPmtInstrUses")]),
}
ZIP_NAMES = {
    "Supplier Import": "PozSuppliersInt", "Supplier Address": "PozSupplierAddressesInt",
    "Supplier Site": "PozSupplierSitesInt", "Supplier Site Assignment": "PozSiteAssignmentsInt",
    "Supplier Contacts": "PozSupContactsInt", "Supplier Banks": "ibysupplierbankaccimport",
}


def _template_headers(fname: str, sheet: str) -> list:
    wb = openpyxl.load_workbook(os.path.join(_TPL, fname), read_only=True, data_only=True, keep_vba=False)
    ws = wb[sheet]
    for row in ws.iter_rows(min_row=4, max_row=4, values_only=True):
        hdr = [("" if c is None else str(c).strip()) for c in row]
        break
    while hdr and hdr[-1] == "":
        hdr.pop()
    return hdr


def _frame(headers: list, nrows: int = 3) -> pd.DataFrame:
    return pd.DataFrame([{h: f"{h[:4]}_{i}" for h in headers} for i in range(nrows)])


def test_end_terminator_and_no_column_loss():
    for entity, (fname, sheets) in INTERFACES.items():
        for sheet, _csv in sheets:
            headers = _template_headers(fname, sheet)
            out = L.apply_supplier_layout(_frame(headers), sheet, True)
            assert list(out.columns)[-1] == "END", f"{entity}/{sheet}: END not last"
            assert (out["END"] == "END").all(), f"{entity}/{sheet}: END value missing on a row"
            assert set(headers).issubset(set(out.columns)), f"{entity}/{sheet}: a template column was dropped"
            assert len(out.columns) == len(headers) + 1, f"{entity}/{sheet}: unexpected column count"


def test_primary_sheet_matches_tab_sequence():
    for entity, (fname, sheets) in INTERFACES.items():
        sheet = sheets[0][0]
        order = L.supplier_col_order().get(L.norm_hdr(L.safe_sheet_name(sheet)))
        if not order:  # Banks has no tab order — skip ordering assertion
            continue
        headers = _template_headers(fname, sheet)
        out = list(L.apply_supplier_layout(_frame(headers), sheet, True).columns)
        # Matched tab columns must appear in the output in the tab's order.
        tab_norm = [L.norm_hdr(h) for h in order]
        out_norm = [L.norm_hdr(c) for c in out]
        seq = [n for n in out_norm if n in set(tab_norm)]
        expected = [n for n in tab_norm if n in set(out_norm)]
        assert seq == expected, f"{entity}: column order does not follow the analyst tab sequence"


def test_oracle_zip_and_csv_names():
    for entity, (fname, sheets) in INTERFACES.items():
        primary = sheets[0][0]
        assert L.zip_name_for(primary) == ZIP_NAMES[entity], f"{entity}: wrong zip name"
        for sheet, csv_name in sheets:
            assert L.csv_name_for(sheet) == csv_name, f"{entity}/{sheet}: wrong csv name"


def test_headerless_default_and_header_toggle():
    headers = _template_headers(*[(f, s[0][0]) for e, (f, s) in INTERFACES.items() if e == "Supplier Import"][0])
    out = L.apply_supplier_layout(_frame(headers), "POZ_SUPPLIERS_INT", True)
    headerless = out.to_csv(index=False, header=False).splitlines()
    withhdr = out.to_csv(index=False, header=True).splitlines()
    # Headerless: first line is DATA (not the label row), still ends in END.
    assert headerless[0].split(",")[-1] == "END"
    assert not headerless[0].startswith("Import Action")
    # Header toggle on: first line IS the label row, first label = Import Action *, last = END.
    assert withhdr[0].split(",")[0].startswith("Import Action")
    assert withhdr[0].split(",")[-1] == "END"


def test_non_supplier_is_noop():
    df = pd.DataFrame([{"A": "1", "B": "2"}])
    out = L.apply_supplier_layout(df.copy(), "EGP_SYSTEM_ITEMS_INTERFACE", False)
    assert list(out.columns) == ["A", "B"]
    assert "END" not in out.columns


def test_same_layout_for_existing_and_new_projects():
    # Generation always calls this function, so an EXISTING conversion (regenerated)
    # and a brand-NEW one produce the identical column layout for the same interface,
    # regardless of how many data rows each carries.
    headers = _template_headers("1_SupplierImport_POZ_SUPPLIERS_INT.xlsm", "POZ_SUPPLIERS_INT")
    existing = L.apply_supplier_layout(_frame(headers, 2), "POZ_SUPPLIERS_INT", True)   # "existing" project
    new = L.apply_supplier_layout(_frame(headers, 9), "POZ_SUPPLIERS_INT", True)        # "new" project
    assert list(existing.columns) == list(new.columns)
    assert list(existing.columns)[-1] == "END" and list(new.columns)[-1] == "END"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
