"""Oracle's column rules, mined from the templates' own header comments.

The templates carry a comment on every header cell stating the database truth for that
column. The parser never read them, and for the TABULAR templates — which the real
Oracle generation workbooks are — that left almost nothing to validate against:
``data_type`` came from whether one sample row happened to hold a number, and
``max_length``, ``format_mask`` and ``allowed_values`` were empty. So the engine had a
length check, a numeric check, a date check and a value-set check, and a live Supplier
conversion reported exactly one kind of error across 156 fields.

Three dialects are in use across the templates on file (4,165 comments surveyed), and
all three are exercised here with text taken verbatim from the workbooks:

  A. Labelled — Supplier:   "Column Name: X / Data Type: VARCHAR2(10 CHAR) / ..."
  B. Bare — Customer:       "BATCH_ID / NOT NULL / NUMBER (18) / Batch identifier."
  C. Value list — BOM:      "TRANSACTION_TYPE / VARCHAR2(10) / Possible Values:- ..."

Nothing here is an inference. Each assertion is what Oracle wrote, which is the reason
these rules are safe to block on.

Pure: stdlib only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.template_comments import (  # noqa: E402
    apply_to_field, parse_comment, ref_to_rowcol,
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


# ── Cell references ─────────────────────────────────────────────────────────
def test_cell_refs_convert_past_the_first_26_columns():
    """Templates are wide — Customer has 88 columns on one sheet — so the two-letter
    case is the normal one, not an edge case."""
    for ref, want in (("A4", (4, 1)), ("Z4", (4, 26)), ("AA4", (4, 27)),
                      ("AB4", (4, 28)), ("BA10", (10, 53))):
        check(f"{ref} -> {want}", ref_to_rowcol(ref) == want,
              f"got {ref_to_rowcol(ref)}")
    check("junk is refused", ref_to_rowcol("nonsense") is None)
    check("empty is refused", ref_to_rowcol("") is None)


# ── Dialect B: Customer. The one from the screenshot. ────────────────────────
def test_customer_batch_id_not_null_number_18():
    p = parse_comment("BATCH_ID\n\nNOT NULL\nNUMBER (18) \n\nBatch identifier.\n")
    check("column", p["db_column"] == "BATCH_ID")
    check("type", p["data_type"] == "NUMBER")
    check("precision 18", p["precision"] == 18, f"got {p.get('precision')}")
    check("NOT NULL -> not nullable", p["nullable"] is False)
    check("description", p["description"] == "Batch identifier.",
          f"got {p.get('description')!r}")


def test_type_on_the_same_line_as_the_column():
    p = parse_comment("PARTY_ORIG_SYSTEM VARCHAR2(30)\n\nOriginal System Identifier "
                      "for the party.")
    check("column", p["db_column"] == "PARTY_ORIG_SYSTEM")
    check("type", p["data_type"] == "VARCHAR2")
    check("length 30", p["length"] == 30)
    check("no NOT NULL means nothing was said, not 'nullable'",
          "nullable" not in p, f"got {p.get('nullable')!r}")


def test_codes_quoted_inline_become_a_value_set():
    p = parse_comment("INSERT_UPDATE_FLAG VARCHAR2(1)\n\nIndicates if a record should "
                      "be explicitly inserted or updated. 'I' for insert, 'U' for update.")
    check("I and U", p["allowed_values"] == ["I", "U"],
          f"got {p.get('allowed_values')}")


# ── Dialect A: Supplier ─────────────────────────────────────────────────────
def test_labelled_supplier_comment():
    p = parse_comment(
        "Column Name: FAX_COUNTRY_CODE\n\nData Type: VARCHAR2(10 CHAR)\n\n"
        "Description: Fax country code to be used for supplier communication.\n\n"
        "Import Actions Supported: CREATE, UPDATE.\n\n"
        "To find a valid Fax Country Code:\n1. Navigate to Setup and Maintenance.")
    check("column", p["db_column"] == "FAX_COUNTRY_CODE")
    check("CHAR qualifier does not become the length",
          p["data_type"] == "VARCHAR2" and p["length"] == 10, f"got {p}")
    check("import actions", p["import_actions"] == "CREATE, UPDATE")
    check("setup hint kept", "To find a valid" in p["setup_hint"])
    check("description is the labelled one",
          p["description"].startswith("Fax country code"), f"got {p['description']!r}")


def test_date_column_carries_its_stated_format():
    p = parse_comment("Column Name: TAX_VERIFICATION_DATE\n\nData Type: DATE\n"
                      "For this column use format YYYY/MM/DD\n\n"
                      "Description: Tax verification date.")
    check("type", p["data_type"] == "DATE")
    check("format read from the comment, not assumed",
          p["date_format"] == "YYYY/MM/DD", f"got {p.get('date_format')}")


def test_a_date_column_with_no_stated_format_gets_oracles_default():
    """Every "use format" in the templates on file says YYYY/MM/DD, so a DATE with no
    explicit format takes it — but it is read where stated rather than hardcoded."""
    p = parse_comment("SOME_DATE\nDATE\n\nA date.")
    check("default", p["date_format"] == "YYYY/MM/DD")


def test_valid_values_prose_becomes_codes():
    p = parse_comment("Column Name: ALLOW_AWT_FLAG\n\nData Type: VARCHAR2(1 CHAR)\n\n"
                      "Description: Indicates whether Withholding Tax is enabled. "
                      "Valid values are Y or N.")
    check("Y and N", p["allowed_values"] == ["Y", "N"], f"got {p.get('allowed_values')}")


# ── Dialect C: BOM / Item ───────────────────────────────────────────────────
def test_possible_values_block_becomes_codes():
    p = parse_comment("TRANSACTION_TYPE\nVARCHAR2(10) \n\nPossible Values:-\n"
                      "CREATE\nUPDATE\nSYNC\n")
    check("three codes", p["allowed_values"] == ["CREATE", "UPDATE", "SYNC"],
          f"got {p.get('allowed_values')}")


# ── The do-not-populate instruction ─────────────────────────────────────────
def test_oracle_telling_you_to_leave_a_column_alone():
    """855 of the Customer template's 1,253 columns say this. It is both a finding
    (a value there is probably a mis-map) and the authority for shipping blank."""
    for text in ("ATTRIBUTE29 VARCHAR2(150)\n\nThis column is not used. Do not provide "
                 "a value for this column.",
                 "X_COL VARCHAR2(10)\n\nDo not provide a value for this column."):
        p = parse_comment(text)
        check("flagged", p.get("do_not_populate") is True, f"got {p}")
    p = parse_comment("SUPPLIER_NAME VARCHAR2(360)\n\nName of the supplier.")
    check("an ordinary column is not flagged", "do_not_populate" not in p)


# ── Things that must NOT be turned into constraints ─────────────────────────
def test_a_type_word_inside_the_description_does_not_retype_the_column():
    """Prose mentioning a type is not a type declaration. Getting this wrong makes a
    text column numeric and then every value fails a check it never should face."""
    p = parse_comment("SUPPLIER_NOTE VARCHAR2(240)\n\nFree text. Enter the DATE the "
                      "supplier was approved and the NUMBER of the agreement.")
    check("stays VARCHAR2", p["data_type"] == "VARCHAR2", f"got {p['data_type']}")
    check("length kept", p["length"] == 240)


def test_prose_is_not_mistaken_for_a_value_list():
    p = parse_comment("COMMENTS VARCHAR2(240)\n\nValid values are determined by the "
                      "business and agreed during design.")
    vals = p.get("allowed_values") or []
    check("no pseudo-codes harvested", not vals, f"got {vals}")


def test_an_empty_or_junk_comment_yields_nothing():
    check("empty", parse_comment("") == {})
    check("whitespace", parse_comment("   \n  ") == {})
    p = parse_comment("see the functional spec")
    check("no column invented", "db_column" not in p, f"got {p}")


def test_a_lower_case_first_word_is_not_a_column_name():
    p = parse_comment("please leave blank\n\nsomething")
    check("no column", "db_column" not in p, f"got {p}")


# ── Merging into a parsed field ─────────────────────────────────────────────
def test_the_comment_fills_what_the_parser_could_not_know():
    """The tabular branch produces this: a type guessed from one sample value and
    nothing else. It is the case that made the validator useless."""
    field = {"field_name": "Batch Identifier", "required": False,
             "data_type": "Character", "max_length": None, "format_mask": None,
             "allowed_values": []}
    out = apply_to_field(field, parse_comment(
        "BATCH_ID\n\nNOT NULL\nNUMBER (18)\n\nBatch identifier."))
    check("type corrected", out["data_type"] == "Number", f"got {out['data_type']}")
    check("precision", out["precision"] == 18)
    check("NOT NULL makes it required", out["required"] is True)
    check("db column recorded", out["db_column"] == "BATCH_ID")
    check("comment kept verbatim for audit", "NOT NULL" in out["comment_text"])


def test_a_header_asterisk_is_never_un_required_by_the_comment():
    """The '*' is Oracle's statement about the LOAD; NOT NULL is about the table. A
    nullable column must not cancel a required header."""
    out = apply_to_field({"field_name": "Supplier Name", "required": True},
                         parse_comment("SUPPLIER_NAME VARCHAR2(360)\n\nName."))
    check("still required", out["required"] is True)


def test_the_parsers_own_max_length_is_not_overwritten():
    """The transposed templates DO carry a Data Type row. Where the parser already
    found a length, that stays — the comment only fills gaps."""
    out = apply_to_field({"field_name": "X", "max_length": 40},
                         parse_comment("X VARCHAR2(150)\n\nDesc."))
    check("kept 40", out["max_length"] == 40, f"got {out['max_length']}")


def test_oracle_types_map_to_the_validators_vocabulary():
    cases = {"VARCHAR2": "Character", "CHAR": "Character", "CLOB": "Character",
             "NUMBER": "Number", "FLOAT": "Number",
             "DATE": "Date", "TIMESTAMP": "Date"}
    for oracle, want in cases.items():
        out = apply_to_field({"field_name": "X"},
                             parse_comment(f"X_COL {oracle}(10)\n\nDesc."))
        check(f"{oracle} -> {want}", out["data_type"] == want,
              f"got {out['data_type']}")


def test_merging_nothing_changes_nothing():
    field = {"field_name": "X", "data_type": "Character", "required": False}
    check("no constraints", apply_to_field(dict(field), None) == field)
    check("empty constraints", apply_to_field(dict(field), {}) == field)


def test_allowed_values_become_the_shape_the_engine_expects():
    out = apply_to_field({"field_name": "Flag"}, parse_comment(
        "ALLOW_FLAG VARCHAR2(1)\n\nValid values are Y or N."))
    check("list of dicts",
          out["allowed_values"] == [{"code": "Y", "meaning": ""},
                                    {"code": "N", "meaning": ""}],
          f"got {out['allowed_values']}")


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
