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


def overlay(directive, values, *, status="suggested", source_column=""):
    """Mirror of the overlay branch in output_service._convert_source."""
    explicit = bool(str(source_column or "").strip()
                    and status in ("approved", "overridden"))
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
