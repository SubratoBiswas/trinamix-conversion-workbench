"""Who wins when a strategy constant meets an analyst's approved mapping.

Reported against engagement eBOS_29_07_2026 (supplier): Tax Organisation Type was
mapped and APPROVED, yet the generated file carried the constant on every row.
Alternate Name behaved the same way.

Cause: the strategy overlay in ``_convert_source`` wrote ``[constant] * n_rows``
unconditionally. ``_apply_control_defaults`` had been given an explicitly-mapped
guard for QA #8; the overlay was written afterwards and never inherited it.

These tests mirror the overlay's decision, which is the part that decides whether
a mapped value survives. Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def overlay(directive, values, *, status="suggested", source_column="",
            approved_by="analyst@nextpower.com"):
    """Mirror of the overlay branch in output_service._convert_source.

    ``approved_by`` defaults to a person because that is what every test below
    was written to mean. An APPROVAL BY THE LEARNING ENGINE is not the same
    thing, and treating the two alike is what let a seeded mapping outrank the
    analyst's own later correction — see the tests at the bottom of this file.
    """
    approver = str(approved_by or "").strip()
    by_a_person = bool(approver) and approver != "learning-engine"
    explicit = bool(str(source_column or "").strip()
                    and status in ("approved", "overridden")
                    and by_a_person)
    col = list(values)
    if not directive:
        return col
    if directive.get("blank") and not explicit:
        return [""] * len(col)
    if "constant" in directive:
        cv = directive["constant"]
        if directive.get("fill_blank_only") or explicit:
            return [v if str(v).strip() else cv for v in col]
        return [cv] * len(col)
    return col


CORP = {"constant": "CORPORATION"}


# ── The reported bug ─────────────────────────────────────────────────────────
def test_approved_mapping_survives_a_strategy_constant():
    got = overlay(CORP, ["PARTNERSHIP", "LLC"],
                  status="approved", source_column="taxorgtype")
    check("mapped values kept", got == ["PARTNERSHIP", "LLC"], f"got {got}")


def test_an_overridden_mapping_wins_too():
    got = overlay(CORP, ["PARTNERSHIP"], status="overridden", source_column="x")
    check("kept", got == ["PARTNERSHIP"], f"got {got}")


def test_blank_rows_are_still_filled_when_mapped():
    """Filling a gap is what the constant is FOR — only wholesale replacement is
    what an explicit mapping overrules."""
    got = overlay(CORP, ["PARTNERSHIP", "", "  "],
                  status="approved", source_column="taxorgtype")
    check("gaps filled, values kept",
          got == ["PARTNERSHIP", "CORPORATION", "CORPORATION"], f"got {got}")


# ── What must NOT change ─────────────────────────────────────────────────────
def test_a_suggested_mapping_does_not_overrule_the_strategy():
    """Auto-map guessing is exactly what the signed constants exist to correct,
    so only a deliberate approve/override wins."""
    got = overlay(CORP, ["PARTNERSHIP", "LLC"],
                  status="suggested", source_column="taxorgtype")
    check("constant still wins", got == ["CORPORATION", "CORPORATION"], f"got {got}")


def test_an_unmapped_field_takes_the_constant():
    got = overlay(CORP, ["", ""], status="suggested", source_column="")
    check("constant applied", got == ["CORPORATION", "CORPORATION"], f"got {got}")


def test_approved_but_with_no_source_column_is_not_explicit():
    """Approving a DEFAULT-only decision is not the same as binding a source, so
    it must not shield the field from the strategy."""
    got = overlay(CORP, ["", ""], status="approved", source_column="")
    check("constant applied", got == ["CORPORATION", "CORPORATION"], f"got {got}")


def test_fill_blank_only_constants_are_unaffected():
    """Payment Method / Payment Terms are seeded blank-only; their behaviour must
    be identical before and after this change."""
    d = {"constant": "G-Treasury", "fill_blank_only": True}
    got = overlay(d, ["ACH", ""], status="suggested", source_column="paymethod")
    check("only the gap filled", got == ["ACH", "G-Treasury"], f"got {got}")


# ── Blank directives ─────────────────────────────────────────────────────────
def test_blank_directive_yields_to_an_approved_mapping():
    got = overlay({"blank": True}, ["Trading As Northwind"],
                  status="approved", source_column="altname")
    check("mapped value survives", got == ["Trading As Northwind"], f"got {got}")


def test_blank_directive_still_blanks_an_unmapped_field():
    got = overlay({"blank": True}, ["Acme Inc"], status="suggested")
    check("blanked", got == [""], f"got {got}")


def test_no_directive_changes_nothing():
    got = overlay(None, ["a", "b"], status="approved", source_column="x")
    check("untouched", got == ["a", "b"], f"got {got}")


# ── Whose approval is it? ────────────────────────────────────────────────────
# The analyst sent back a generated file in which three fields their own 30-Jul
# corrections declare BLANK were populated on every row: Supplier Name New
# (3,872 rows, carrying the supplier name), Procurement BU (5,315, "Nextracker
# Consolidated") and Liability Distribution (1,528, an account string). All three
# had a SEEDED source column that the learning engine had approved, and an
# approved mapping skipped the blank rule. So the correction was correct, present,
# and silently outranked by the thing it was written to correct.
def test_an_engine_approved_mapping_does_not_defeat_a_blank_correction():
    got = overlay({"blank": True}, ["Acme Inc", "Beta Ltd"],
                  status="approved", source_column="companyname",
                  approved_by="learning-engine")
    check("the correction wins over a seeded mapping", got == ["", ""], f"got {got}")


def test_a_person_approving_still_beats_the_correction():
    """The analyst's own rule, 30-Jul: 'If user modifies anything from the tool UI
    and then approves it, it should get highest precedence.'"""
    got = overlay({"blank": True}, ["Trading As Northwind"],
                  status="approved", source_column="altname",
                  approved_by="subrato.biswas@nextpower.com")
    check("the person wins", got == ["Trading As Northwind"], f"got {got}")


def test_an_engine_approved_mapping_does_not_shield_a_constant_either():
    """Same rule for constants, or Business Relationship would keep whatever a
    seeded mapping put there instead of SPEND_AUTHORIZED."""
    got = overlay({"constant": "SPEND_AUTHORIZED"}, ["PROSPECTIVE", "PROSPECTIVE"],
                  status="approved", source_column="relationship",
                  approved_by="learning-engine")
    check("the correction's constant wins",
          got == ["SPEND_AUTHORIZED", "SPEND_AUTHORIZED"], f"got {got}")


def test_an_approval_with_no_recorded_approver_is_not_treated_as_a_person():
    """Rows predating approved_by carry nothing. Reading those as human approvals
    would restore the exact bug; the conservative reading is that the analyst's
    written correction wins, which is also what they asked for."""
    got = overlay({"blank": True}, ["Acme Inc"], status="approved",
                  source_column="companyname", approved_by="")
    check("blanked", got == [""], f"got {got}")


def test_the_service_actually_makes_this_distinction():
    """Seam: this file is a mirror, and a mirror that has drifted proves nothing."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "services" / "output_service.py").read_text(encoding="utf-8")
    body = src.split("# ── Strategy overlay (write-time guarantee)")[1][:2600]
    check("the overlay reads approved_by", "approved_by" in body)
    check("and excludes the learning engine", '!= "learning-engine"' in body)
    check("and _explicit requires a person", "_by_a_person" in body)



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
