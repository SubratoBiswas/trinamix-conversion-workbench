"""Customer merge relocated to the domain (Phase 4, slice 1), pinned.

The ~1,460-line pure module moved verbatim to ``app.domain.customer.merge``; the old
``app.services.customer_merge`` path is now a re-export shim. These tests pin that the
shim exposes the same objects (by identity — so the ``_OWNED_OVERRIDE`` ContextVar still
works across the seam) and spot-check a few representative pure functions so a future
edit that breaks the relocation is caught.
"""
import os
import sys
import contextvars

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import customer_merge as SHIM
from app.domain.customer import merge as DOM


# --- the relocation itself ---------------------------------------------------

def test_shim_reexports_the_domain_objects_by_identity():
    # a representative spread of public + private names the callers reach for
    for name in ("GRAIN_COL", "ENTITYID_COL", "BORROWABLE_SRC_COLS", "DFF_SOURCE_COLS",
                 "build_entity_enrichment", "set_party_ref_from_master", "sheet_rows",
                 "merge_owned_fields", "sheet_blank_fields", "_norm", "_GLUE_OWNED"):
        assert getattr(SHIM, name) is getattr(DOM, name), name


def test_owned_override_is_one_contextvar_across_the_seam():
    assert isinstance(DOM._OWNED_OVERRIDE, contextvars.ContextVar)
    # the whole point: a .set() through the shim is visible to the domain reader
    assert SHIM._OWNED_OVERRIDE is DOM._OWNED_OVERRIDE
    tok = SHIM._OWNED_OVERRIDE.set(frozenset({"partytype"}))
    try:
        assert DOM._OWNED_OVERRIDE.get() == frozenset({"partytype"})
    finally:
        SHIM._OWNED_OVERRIDE.reset(tok)


def test_grain_constants():
    assert DOM.GRAIN_COL == "__grain"
    assert (DOM.PARTY, DOM.SITE, DOM.CONTACT) == ("party", "site", "contact")


# --- representative pure behaviour (via the shim, as callers see it) ----------

def test_sheet_grain_maps_interface_sheets_to_their_source_grain():
    assert SHIM.sheet_grain("HZ_IMP_PARTIES_T") == DOM.sheet_grain("HZ_IMP_PARTIES_T")
    # a sheet the merge does not recognise resolves to None (returned untouched upstream)
    assert SHIM.sheet_grain("NOT_A_SHEET") is None


def test_classify_source_columns_by_anchor():
    assert SHIM.classify_source_columns(["entityid", "organizationname"]) == DOM.PARTY
    assert SHIM.classify_source_columns(["entityid", "addressline1"]) == DOM.SITE
    assert SHIM.classify_source_columns(["entityid", "personfirstname", "personlastname"]) == DOM.CONTACT
    assert SHIM.classify_source_columns(["foo", "bar"]) is None


def test_merge_owned_fields_and_blank_fields_are_sets():
    assert isinstance(SHIM.merge_owned_fields("HZ_IMP_PARTIES_T"), set)
    assert isinstance(SHIM.sheet_blank_fields("HZ_IMP_ACCTSITEUSES_T"), set)
    assert SHIM.first_flag_field("HZ_IMP_PARTYSITES_T") == "Identifying Address"
    assert SHIM.first_flag_field("HZ_IMP_CONTACTPTS_T") is None


def test_build_entity_enrichment_first_nonblank_wins():
    master = pd.DataFrame({"entityid": ["NT-1"], "startdate": ["2020-01-01"], "language": ["en"]})
    child = pd.DataFrame({"entityid": ["NT-1"], "startdate": ["2021-01-01"]})
    enr = SHIM.build_entity_enrichment([master, child])   # master arrives first -> wins
    assert enr["startdate"]["NT-1"] == "2020-01-01"


def test_set_party_ref_from_master_stamps_customer_internalid_on_children():
    df = pd.DataFrame([
        {"entityid": "NT-1", DOM.GRAIN_COL: "party",   DOM.ENTITYID_COL: "NT-1", DOM.INTERNALID_COL: "101"},
        {"entityid": "NT-1", DOM.GRAIN_COL: "site",    DOM.ENTITYID_COL: "NT-1", DOM.INTERNALID_COL: "201"},
    ])
    out = SHIM.set_party_ref_from_master(df)
    # both rows now carry the SAME party ref (the master's internalid), not the child's own
    refs = set(out[DOM.PARTYREF_COL].astype(str))
    assert refs == {"101"}
