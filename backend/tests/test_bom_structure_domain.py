"""BOM structure reshape relocated to the domain (Phase 4, slice 2), pinned.

The ~274-line pure module moved verbatim to ``app.domain.bom.structure``; the old
``app.services.bom_structure_service`` path is now a re-export shim. These tests pin the
shim identity and spot-check the pure behaviour so a broken relocation is caught.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import bom_structure_service as SHIM
from app.domain.bom import structure as DOM


def test_shim_reexports_the_domain_objects_by_identity():
    for name in ("reshape_for_sheet", "missing_mandatory", "MANDATORY", "_kind", "_BOM_BATCH_VALUE"):
        assert getattr(SHIM, name) is getattr(DOM, name), name


def test_kind_maps_sheet_names_to_bom_interfaces():
    assert DOM._kind("EGP_STRUCTURES_INTERFACE") == "structures"
    assert DOM._kind("EGP_STRUCTURE_COMPONENTS_INT") == "components"
    assert DOM._kind("BOM_SUBSTITUTE_COMPONENTS") == "substitutes"
    assert DOM._kind("BOM_REF_DESIGNATORS") == "reference_designators"
    assert DOM._kind("something_else") is None


def test_mandatory_has_an_entry_per_interface():
    assert set(DOM.MANDATORY) == {"structures", "components", "substitutes", "reference_designators"}


def test_missing_mandatory_reports_absent_columns_via_the_shim():
    # a structures frame missing Effective Date + Item Name
    df = pd.DataFrame({"Transaction Type": ["CREATE"], "Batch Number": ["900001"],
                       "Structure Name": ["S1"], "Organization Code": ["ORG"]})
    missing = SHIM.missing_mandatory(df, "EGP_STRUCTURES_INTERFACE")
    assert "Item Name" in missing and "Effective Date" in missing
    # an unrecognised sheet has no mandatory set -> nothing reported
    assert SHIM.missing_mandatory(df, "not_a_bom_sheet") == []


def test_reshape_for_sheet_returns_a_frame_and_leaves_unknown_sheets_untouched():
    df = pd.DataFrame({"Assembly Item": ["A", "A"], "Component Item": ["x", "y"]})
    out = SHIM.reshape_for_sheet(df.copy(), "unknown_sheet")
    # defensive: an unrecognised sheet is returned unchanged
    assert list(out.columns) == list(df.columns) and len(out) == len(df)
    # a recognised sheet still yields a DataFrame
    assert isinstance(SHIM.reshape_for_sheet(df.copy(), "EGP_STRUCTURES_INTERFACE"), pd.DataFrame)
