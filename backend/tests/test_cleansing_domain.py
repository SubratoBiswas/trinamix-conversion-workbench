"""Cleansing rules relocated to the domain (Phase 4, slice 3), pinned.

The ~393-line pure module moved verbatim to ``app.domain.cleansing``; the old
``app.services.cleansing_rules`` path is now a re-export shim. These tests pin the shim
identity and spot-check the pure cleansing behaviour so a broken relocation is caught.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import cleansing_rules as SHIM
from app.domain import cleansing as DOM


def test_shim_reexports_the_domain_objects_by_identity():
    for name in ("cleanse_frame", "default_profile", "is_text_column", "_norm_protect",
                 "FAMILIES", "SAFE_FAMILIES", "_apply_whitespace_punct"):
        assert getattr(SHIM, name) is getattr(DOM, name), name


def test_whitespace_and_punct_collapse():
    # collapses internal runs AND strips edge punctuation (the trailing '.') and spaces
    assert SHIM._apply_whitespace_punct("  Acme   Inc.  ") == "Acme Inc"


def test_special_char_folding_optional_ascii():
    plain = SHIM._apply_special("café", ascii_fold=False)
    folded = SHIM._apply_special("café", ascii_fold=True)
    assert "é" in plain            # not folded by default
    assert folded == "cafe"        # folded on request


def test_is_text_column_keys_on_dtype_not_content():
    # a string (object) column is text even if the strings look numeric...
    assert SHIM.is_text_column(pd.Series(["Acme", "Beta Corp", "O'Brien"])) is True
    assert SHIM.is_text_column(pd.Series(["1", "2", "3"])) is True
    # ...but a numeric-DTYPE column is not
    assert SHIM.is_text_column(pd.Series([1, 2, 3])) is False


def test_default_profile_covers_the_columns():
    prof = SHIM.default_profile(["Supplier Name", "Currency Code", "Amount"])
    assert isinstance(prof, dict)
    # the safe families are always available in the profile vocabulary
    assert set(DOM.SAFE_FAMILIES) <= set(DOM.FAMILIES)


def test_cleanse_frame_is_opt_in_and_applies_enabled_families():
    df = pd.DataFrame({"Supplier Name": ["  Acme   Inc.  ", "AT&T  Corporation"]})
    # the DEFAULT profile enables no families -> cleanse_frame is a no-op
    out, findings = SHIM.cleanse_frame(df.copy())
    assert list(out["Supplier Name"]) == ["  Acme   Inc.  ", "AT&T  Corporation"]
    assert findings == []
    # a profile that enables whitespace_punct actually cleanses, and reports it
    prof = {"families": ["whitespace_punct"], "ascii_fold": False,
            "per_field": {}, "exclude_fields": [], "value_overrides": {}}
    out2, findings2 = SHIM.cleanse_frame(df.copy(), prof)
    assert out2["Supplier Name"].iloc[0] == "Acme Inc"
    assert any(f["field"] == "Supplier Name" and f["rule"] == "whitespace_punct" for f in findings2)


def test_cleanse_frame_empty_is_a_noop():
    empty = pd.DataFrame()
    out, findings = SHIM.cleanse_frame(empty)
    assert len(out) == 0 and findings == []
