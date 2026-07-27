"""Unit tests for Issue #4 — better tokenization lifts genuine matches without
inflating unrelated ones (app.ai.rule_based)."""
import dataclasses
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.ai.rule_based import _tokenize, score_pair  # noqa: E402
from app.ai.base import SourceColumn, TargetField  # noqa: E402


def _sc(name, itype="string", null=0.0, samples=None):
    fields = {f.name for f in dataclasses.fields(SourceColumn)}
    kw = {k: v for k, v in [("name", name), ("inferred_type", itype),
                            ("null_percent", null), ("sample_values", samples or [])] if k in fields}
    return SourceColumn(**kw)


def _tf(name, dtype="Character", required=False, desc=""):
    fields = {f.name for f in dataclasses.fields(TargetField)}
    kw = {k: v for k, v in [("id", "1"), ("field_name", name), ("data_type", dtype),
                            ("required", required), ("description", desc), ("max_length", None)] if k in fields}
    return TargetField(**kw)


def test_noise_prefix_stripped():
    assert _tokenize("custitem_lifecycle_phase") == ["lifecycle", "phase"]
    assert _tokenize("custentity_credit_hold") == ["credit", "hold"]


def test_glued_id_split_on_known_stems():
    assert _tokenize("itemid") == ["item", "id"]
    assert _tokenize("entityid") == ["entity", "id"]


def test_no_false_id_split():
    # words that merely end in "id" must NOT split
    assert _tokenize("valid") == ["valid"]
    assert _tokenize("void") == ["void"]


def test_genuine_match_scores_high():
    # Normalised composite + fuzzy coverage lifts an exact-token match well clear
    # of the old ~0.6 ceiling (was the sub-50% complaint for Item).
    s, reasons = score_pair(_sc("custitem_lifecycle_phase"), _tf("Lifecycle Phase"))
    assert s >= 0.75, (s, reasons)


def test_unrelated_stays_low_and_below_genuine():
    good, _ = score_pair(_sc("custitem_lifecycle_phase"), _tf("Lifecycle Phase"))
    bad, _ = score_pair(_sc("createddate", "date"), _tf("Lifecycle Phase"))
    assert bad < 0.35            # no inflation of unrelated columns
    assert good > bad + 0.4      # genuine clearly beats unrelated


def test_id_match_surfaces():
    # itemid -> Item Number: was buried ~0.5; now a confident, review-worthy match.
    s, _ = score_pair(_sc("itemid"), _tf("Item Number"))
    assert s >= 0.6


def test_fuzzy_abbreviation_matches():
    # An abbreviation with NO dictionary alias still matches via prefix/fuzzy.
    s, _ = score_pair(_sc("descr"), _tf("Description"))
    assert s >= 0.6


def test_partial_token_coverage_is_meaningful():
    # A single source token covering one of two target tokens is a real (not
    # buried) suggestion — soft coverage keeps it above the noise floor.
    s, _ = score_pair(_sc("item"), _tf("Item Number"))
    assert s >= 0.4


def test_missing_lov_does_not_dilute():
    # A name+semantic+type match on a field with NO list-of-values and NO samples
    # is not dragged down by the (inapplicable) value/LOV weight.
    s, _ = score_pair(_sc("manufacturer_name"), _tf("Manufacturer Name"))
    assert s >= 0.7


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
