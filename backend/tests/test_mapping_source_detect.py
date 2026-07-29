"""Detecting which legacy system a mapping workbook maps FROM.

Deterministic path only — no network, no credentials. The AI fallback is a
separate code path that only runs when everything here returns nothing.

Fixtures use the real shapes: a dedicated source-system column, a source column
whose header names the system, the side-by-side Oracle / NetSuite / eBOS grid,
and files where only the sheet or file name gives it away.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mapping_source_detect import detect_source_systems  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def systems(*a, **kw):
    return [h["system"] for h in detect_source_systems(*a, **kw)]


# ── The source column's own header ───────────────────────────────────────────
def test_source_header_names_the_system():
    h = ["Oracle Field", "NetSuite Column", "Comments"]
    check("netsuite from the source header",
          systems(h, source_column=1) == ["netsuite"], f"got {systems(h, source_column=1)}")


def test_syteline_spellings():
    for header in ("SyteLine Field", "Infor SyteLine Column", "Site Line Attribute"):
        got = systems(["Oracle Field", header], source_column=1)
        check(f"{header!r} -> syteline", got == ["syteline"], f"got {got}")


def test_ebos_is_a_custom_legacy_system():
    """eBOS is the client's own system; it has no catalogue entry of its own."""
    got = systems(["Oracle Field", "eBOS Column"], source_column=1)
    check("eBOS -> custom", got == ["custom"], f"got {got}")


# ── The target must never be read as a source ────────────────────────────────
def test_fusion_headers_are_not_sources():
    """Matching the target column would import every mapping backwards.

    The second column is deliberately neutral: "Legacy Column" is itself a
    recognised alias for a client-specific system, so using it here would test
    the fixture rather than the guard.
    """
    for header in ("Oracle Fusion Field", "FBDI Column", "Target Field",
                   "Oracle Attribute"):
        got = systems([header, "Column B"], source_column=1)
        check(f"{header!r} yields no source", got == [], f"got {got}")


def test_oracle_ebs_is_still_a_real_source():
    """EBS names Oracle but IS a legacy source — the target guard must not eat it."""
    got = systems(["Oracle Field", "Oracle EBS Column"], source_column=1)
    check("Oracle EBS -> oracle_ebs", got == ["oracle_ebs"], f"got {got}")


# ── Side-by-side, multi-source layouts ───────────────────────────────────────
def test_several_systems_on_one_sheet():
    """The real Oracle-NetSuite-SyteLine grid: one dropdown cannot describe it."""
    h = ["Oracle Field Name", "NetSuite Column", "SyteLine Field", "eBOS Column"]
    got = systems(h, source_column=1)
    check("all three legacy systems found",
          set(got) == {"netsuite", "syteline", "custom"}, f"got {got}")
    check("the source column's own system ranks first", got[0] == "netsuite",
          f"got {got}")


def test_target_column_is_excluded_from_the_sweep():
    h = ["Oracle Fusion Field", "NetSuite Column"]
    got = systems(h, source_column=1)
    check("only netsuite", got == ["netsuite"], f"got {got}")


# ── An explicit source-system column beats every inference ───────────────────
def test_system_column_values_win():
    h = ["Source Field", "Target Field", "Source System"]
    rows = [["itemid", "Item Number", "NetSuite"],
            ["item_no", "Item Number", "NetSuite"],
            ["ITEM", "Item Number", "Infor SyteLine"]]
    hits = detect_source_systems(h, source_column=0, system_column=2, rows=rows)
    check("both systems reported", {x["system"] for x in hits} == {"netsuite", "syteline"},
          f"got {[x['system'] for x in hits]}")
    check("the more frequent one leads", hits[0]["system"] == "netsuite",
          f"got {hits[0]}")
    check("method recorded as system_column", hits[0]["method"] == "system_column")
    check("confidence is high", hits[0]["confidence"] == "high")


# ── Weaker evidence: sheet and file names ────────────────────────────────────
def test_sheet_name_then_file_name():
    h = ["Source Field", "Target Field"]
    got = detect_source_systems(h, source_column=0, sheet_name="NetSuite Item Map")
    check("sheet name works", got and got[0]["system"] == "netsuite", f"got {got}")
    check("method is sheet_name", got[0]["method"] == "sheet_name")

    got = detect_source_systems(h, source_column=0,
                                file_name="NXT Supplier Mapping SyteLine.xlsx")
    check("file name works", got and got[0]["system"] == "syteline", f"got {got}")
    check("and is the weakest confidence", got[0]["confidence"] == "low")


def test_stronger_evidence_outranks_weaker():
    h = ["Oracle Field", "NetSuite Column"]
    got = systems(h, source_column=1, sheet_name="SyteLine Extract")
    check("the source header leads over the sheet name", got[0] == "netsuite",
          f"got {got}")


# ── False positives are worse than no answer ─────────────────────────────────
def test_two_letter_aliases_need_a_whole_header():
    """'ns' inside 'Transactions' is not NetSuite."""
    got = systems(["Oracle Field", "Transactions Column"], source_column=1)
    check("no spurious netsuite", "netsuite" not in got, f"got {got}")
    got = systems(["Oracle Field", "NS"], source_column=1)
    check("a bare NS header does match", got == ["netsuite"], f"got {got}")


def test_nothing_recognised_returns_empty():
    """Empty is the signal that the model should be asked."""
    got = systems(["Column A", "Column B"], source_column=0)
    check("no guess made", got == [], f"got {got}")


def test_blank_and_missing_inputs_are_safe():
    check("no headers", detect_source_systems([], source_column=None) == [])
    check("blank headers", systems(["", None, "  "], source_column=0) == [])


def test_each_system_reported_once():
    h = ["NetSuite Field", "NetSuite Column", "NetSuite Attribute"]
    got = systems(h, source_column=0)
    check("deduplicated", got == ["netsuite"], f"got {got}")


def test_evidence_is_recorded_for_review():
    hits = detect_source_systems(["Oracle Field", "NetSuite Column"], source_column=1)
    check("evidence names the header", "NetSuite Column" in hits[0]["evidence"],
          f"got {hits[0]}")
    check("column index kept", hits[0]["column_index"] == 1)


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
