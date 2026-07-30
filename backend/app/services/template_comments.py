"""Mine Oracle's own column rules out of the FBDI template cell comments.

WHY THIS EXISTS
---------------
Every Oracle FBDI generation template carries a comment on each header cell that
states the database truth about that column:

    BATCH_ID

    NOT NULL
    NUMBER (18)

    Batch identifier.

The parser never read them. Worse, for the *tabular* templates — which is what the
real Oracle generation workbooks are — it set ``data_type`` from whether the sample
row happened to hold a number, and left ``max_length``, ``format_mask`` and
``allowed_values`` empty. So the validation engine, which already knows how to check
Exceeds Max Length / Invalid Number Format / Invalid Date Format / Value Not In Value
Set, had almost nothing to check WITH. That is why a live Supplier conversion
reported exactly one kind of validation error over 156 fields.

Nothing here invents a rule. Every constraint is a transcription of what Oracle
wrote in its own template, which is also why it can be trusted enough to block on.

THREE DIALECTS, ALL IN USE
--------------------------
Oracle is not consistent across product families, and all three shapes appear in the
templates on file (4,165 comments surveyed):

  A. Labelled — Supplier (1,046 comments)
        Column Name: FAX_COUNTRY_CODE
        Data Type: VARCHAR2(10 CHAR)
        Description: ...
        Import Actions Supported: CREATE, UPDATE.

  B. Bare, type on its own line or inline — Customer (NOT NULL lives here)
        BATCH_ID
        NOT NULL
        NUMBER (18)
        Batch identifier.
     ...or:  PARTY_ORIG_SYSTEM VARCHAR2(30)

  C. Column, type, then an explicit value list — BOM / Item
        TRANSACTION_TYPE
        VARCHAR2(10)
        Possible Values:-
        CREATE
        UPDATE
        SYNC

Pure: stdlib only (zipfile + ElementTree). No openpyxl — ``read_only=True``
discards comments entirely, and reading a 20-sheet workbook without it to reach a
few hundred strings is slow enough to matter on a small instance.
"""
from __future__ import annotations

import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# ── Cell references ─────────────────────────────────────────────────────────
_REF = re.compile(r"^([A-Z]+)(\d+)$")


def ref_to_rowcol(ref: str) -> tuple[int, int] | None:
    """``"AB4"`` -> ``(4, 28)``. 1-based, matching the parser's own indexing."""
    m = _REF.match(str(ref or "").strip().upper())
    if not m:
        return None
    letters, row = m.group(1), int(m.group(2))
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - 64)
    return (row, col)


# ── OOXML extraction ────────────────────────────────────────────────────────
def extract_comments(path: str) -> dict[str, dict[str, str]]:
    """``{sheet name: {cell ref: comment text}}``.

    Walks workbook -> sheet rels -> comments part, so sheet NAMES come out rather
    than ``sheet3.xml``: the caller matches on the interface-table name.
    Degrades to ``{}`` on anything unreadable — a template with no comments is
    normal (the bundled Item workbook has none at all) and must not fail an upload.
    """
    out: dict[str, dict[str, str]] = {}
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "xl/workbook.xml" not in names:
                return {}
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rid2tgt: dict[str, str] = {}
            if "xl/_rels/workbook.xml.rels" in names:
                for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                    rid2tgt[rel.get("Id")] = rel.get("Target") or ""
            for sh in wb.iter(_M + "sheet"):
                sheet_name = sh.get("name") or ""
                target = rid2tgt.get(sh.get(_R + "id"), "").replace("../", "")
                if not target:
                    continue
                base = ("xl/" + target.lstrip("/")).split("/")[-1]
                rels_part = f"xl/worksheets/_rels/{base}.rels"
                if rels_part not in names:
                    continue
                cpart = None
                for rel in ET.fromstring(z.read(rels_part)):
                    if (rel.get("Type") or "").endswith("/comments"):
                        cpart = "xl/" + (rel.get("Target") or "").replace("../", "").lstrip("/")
                if not cpart or cpart not in names:
                    continue
                found: dict[str, str] = {}
                for c in ET.fromstring(z.read(cpart)).iter(_M + "comment"):
                    ref = c.get("ref")
                    if not ref:
                        continue
                    # Comment text is split across <r><t> runs by formatting; the
                    # concatenation is the text a user sees in the tooltip.
                    txt = "".join(t.text or "" for t in c.iter(_M + "t"))
                    if txt.strip():
                        found[ref] = txt
                if found:
                    out[sheet_name] = found
    except Exception:                                           # noqa: BLE001
        return {}
    return out


# ── Comment text -> constraints ─────────────────────────────────────────────
# An Oracle column name as it appears at the head of a bare-dialect comment. Most are
# UPPER_SNAKE (BATCH_ID, ATTRIBUTE24) but the Customer template also writes 83 of them
# in Mixed_Case (Global_Attribute1), so case alone cannot be the test. Requiring an
# underscore or a digit — or that the whole token is upper-case — accepts every real
# name in the templates on file and rejects prose: an earlier version upper-cased the
# token before testing an upper-case pattern, which accepted everything, so "please
# leave blank" became the column PLEASE.
_DB_COL = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,}$")


def _looks_like_db_column(tok: str) -> bool:
    if not tok or not _DB_COL.match(tok):
        return False
    return tok.isupper() or "_" in tok or any(ch.isdigit() for ch in tok)
_TYPE = re.compile(
    r"\b(VARCHAR2|NVARCHAR2|CHAR|NUMBER|DATE|TIMESTAMP|CLOB|FLOAT|LONG|INTEGER)\b"
    r"(?:\s*\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?(?:CHAR|BYTE)?\s*\))?", re.I)
_NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.I)
_USE_FORMAT = re.compile(r"use\s+format\s+([A-Za-z][A-Za-z/\-\.]{5,20})", re.I)
_VALID_VALUES = re.compile(
    r"[Vv]alid values?\s+(?:are|is)\s*:?\s*([^.\n]{1,200})", re.I)
_POSSIBLE = re.compile(r"Possible\s+Values?\s*:?-?\s*\n(.*?)(?:\n\s*\n|$)",
                       re.I | re.S)
_QUOTED_CODE = re.compile(r"'([A-Za-z0-9_\- ]{1,30})'\s+for\s+([a-z ]{2,30})", re.I)
_DO_NOT_USE = re.compile(
    r"(this column is not used|do not provide a value|not currently used|"
    r"should not be populated)", re.I)
_SETUP_HINT = re.compile(r"To find (?:a |the )?valid[^\n]*", re.I)
_IMPORT_ACTIONS = re.compile(
    r"Import Actions?\s+Supported\s*:?\s*([^\n]{1,120})", re.I)

# "Valid values are Y or N" -> ["Y", "N"]. Split on the connectives Oracle uses,
# then keep only tokens that look like codes: a sentence fragment is not a value.
_VALUE_SPLIT = re.compile(r"\s*(?:,|/|\bor\b|\band\b|;)\s*", re.I)
_CODE_LIKE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\- ]{0,28}$")
_NOT_A_CODE = {"n a", "na", "null", "blank", "none", "etc", "the", "any", "all",
               "yes no", "true false", "a valid value"}


def _clean_codes(raw: str) -> list[str]:
    codes: list[str] = []
    for tok in _VALUE_SPLIT.split(str(raw or "")):
        t = tok.strip().strip("'\"").strip()
        if not t or not _CODE_LIKE.match(t):
            continue
        if t.lower() in _NOT_A_CODE or len(t) > 30:
            continue
        # A multi-word fragment is prose, not a code — except deliberate two-word
        # Oracle codes, which are upper-case throughout ("BILL TO").
        if " " in t and not t.isupper():
            continue
        if t not in codes:
            codes.append(t)
    return codes


def parse_comment(text: str) -> dict[str, Any]:
    """One header-cell comment -> the constraints it states.

    Every key is optional: absent means "Oracle did not say", which is different
    from "no constraint" and must not be turned into one.
    """
    t = str(text or "")
    if not t.strip():
        return {}
    out: dict[str, Any] = {"comment_text": t.strip()[:4000]}
    lines = [ln.strip() for ln in t.splitlines()]
    body = [ln for ln in lines if ln]

    # ── Column name: labelled, else the first line, else the first token ──
    m = re.search(r"Column\s+Name\s*:\s*([A-Z][A-Z0-9_]*)", t, re.I)
    if m:
        out["db_column"] = m.group(1).upper()
    elif body:
        head = body[0]
        first_tok = head.split()[0].strip() if head.split() else ""
        if _looks_like_db_column(first_tok):
            # Kept as written. Upper-casing would make Global_Attribute1 into
            # GLOBAL_ATTRIBUTE1, which is not the name in the interface table.
            out["db_column"] = first_tok

    # ── Data type. Prefer the labelled form; otherwise the first type token that
    # is NOT inside the free-text description (so a description mentioning "DATE"
    # cannot retype a VARCHAR2 column). ──
    type_src = None
    m = re.search(r"Data\s+Type\s*:\s*([^\n]+)", t, re.I)
    if m:
        type_src = m.group(1)
    else:
        # Search only the head of the comment: the column name line and the two
        # lines after it. That is where dialects B and C put the type.
        head_zone = "\n".join(body[:3])
        type_src = head_zone
    tm = _TYPE.search(type_src or "")
    if tm:
        out["data_type"] = tm.group(1).upper()
        if tm.group(2):
            n = int(tm.group(2))
            if out["data_type"] in ("VARCHAR2", "NVARCHAR2", "CHAR"):
                out["length"] = n
            else:
                out["precision"] = n
                if tm.group(3):
                    out["scale"] = int(tm.group(3))

    # ── Nullability. NOT NULL is Oracle stating the column is mandatory at the
    # database level, which is stronger than the header's '*' marker and is the
    # only signal for templates whose headers carry no asterisk. ──
    if _NOT_NULL.search(t):
        out["nullable"] = False

    # ── Date format. Only ever "use format YYYY/MM/DD" in the templates on file,
    # but read it rather than hardcode it — a future template may differ. ──
    m = _USE_FORMAT.search(t)
    if m:
        out["date_format"] = m.group(1).strip().rstrip(".")
    elif out.get("data_type") in ("DATE", "TIMESTAMP"):
        out["date_format"] = "YYYY/MM/DD"

    # ── Accepted values, from any of the three spellings Oracle uses. ──
    codes: list[str] = []
    m = _VALID_VALUES.search(t)
    if m:
        codes += _clean_codes(m.group(1))
    m = _POSSIBLE.search(t)
    if m:
        for ln in m.group(1).splitlines():
            codes += _clean_codes(ln)
    for code, _meaning in _QUOTED_CODE.findall(t):
        c = code.strip()
        if c and c not in codes:
            codes.append(c)
    if codes:
        # A "value list" the width of the column's whole domain is not a list.
        out["allowed_values"] = codes[:60]

    # ── Oracle explicitly telling you to leave it alone. 700+ DFF attribute
    # columns say this; populating one is a defect the analyst wants to hear about,
    # and it is also the authority for shipping the column blank. ──
    if _DO_NOT_USE.search(t):
        out["do_not_populate"] = True

    m = _IMPORT_ACTIONS.search(t)
    if m:
        out["import_actions"] = m.group(1).strip().rstrip(".")
    m = _SETUP_HINT.search(t)
    if m:
        out["setup_hint"] = m.group(0).strip()[:300]

    # ── Description: the labelled form, else the longest prose line. ──
    m = re.search(r"Description\s*:\s*(.+?)(?:\n\s*\n|$)", t, re.S | re.I)
    if m:
        out["description"] = " ".join(m.group(1).split())[:1000]
    else:
        # The longest line that is prose rather than a declaration. The threshold used
        # to be 25 characters, which silently dropped short real descriptions —
        # "Batch identifier." is 17. Excluding the type and NOT NULL lines is what
        # separates prose from declaration; length is a poor proxy for it.
        prose = [ln for ln in body[1:]
                 if len(ln) > 4
                 and not _TYPE.fullmatch(ln.strip())
                 and not _NOT_NULL.fullmatch(ln.strip())
                 and not _TYPE.match(ln.strip())
                 and not ln.strip().upper().startswith("NOT NULL")]
        if prose:
            out["description"] = " ".join(max(prose, key=len).split())[:1000]
    return out


def constraints_by_sheet(path: str) -> dict[str, dict[int, dict]]:
    """``{sheet name: {column index (1-based): constraints}}`` for one workbook.

    Keyed by column index because that is what survives the header-row detection in
    the parser; the comment's row number is kept so a caller can confirm it sits on
    the header row rather than on a sample row.
    """
    out: dict[str, dict[int, dict]] = {}
    for sheet, cells in extract_comments(path).items():
        per_col: dict[int, dict] = {}
        for ref, text in cells.items():
            rc = ref_to_rowcol(ref)
            if not rc:
                continue
            row, col = rc
            parsed = parse_comment(text)
            if not parsed:
                continue
            parsed["_row"] = row
            # Lowest row wins: Oracle puts the authoritative comment on the header
            # cell, and anything further down is an example annotation.
            if col not in per_col or row < per_col[col]["_row"]:
                per_col[col] = parsed
        if per_col:
            out[sheet] = per_col
    return out


# ── Merging into a parsed field ─────────────────────────────────────────────
_MERGE_KEYS = ("db_column", "nullable", "precision", "scale", "do_not_populate",
               "import_actions", "setup_hint", "comment_text")


def apply_to_field(field: dict, cons: dict | None) -> dict:
    """Fold one column's mined constraints into a parsed field dict, in place.

    The parser's own findings win where it actually HAS one — a header '*' marks a
    field required even if the comment says the database column is nullable, because
    the asterisk is Oracle's statement about the LOAD and NOT NULL is about the
    table. Everything the parser left empty is filled from the comment.
    """
    if not cons:
        return field
    for k in _MERGE_KEYS:
        if cons.get(k) is not None:
            field[k] = cons[k]
    # data_type: the comment is authoritative. The tabular branch guesses from
    # whether one sample row held a number, which is how a VARCHAR2(30) column of
    # numeric-looking ids became "Number" and every value then failed the numeric
    # check it should never have been subject to.
    if cons.get("data_type"):
        field["data_type"] = _friendly_type(cons["data_type"])
    if cons.get("length") and not field.get("max_length"):
        field["max_length"] = cons["length"]
    if cons.get("date_format") and not field.get("format_mask"):
        field["format_mask"] = cons["date_format"]
    if cons.get("allowed_values") and not field.get("allowed_values"):
        field["allowed_values"] = [{"code": c, "meaning": ""}
                                   for c in cons["allowed_values"]]
    if cons.get("description") and not field.get("description"):
        field["description"] = cons["description"]
    # NOT NULL makes it required; a nullable column never UN-requires a '*' header.
    if cons.get("nullable") is False:
        field["required"] = True
    notes = [n for n in (field.get("validation_notes"),
                         cons.get("setup_hint")) if n]
    if notes:
        field["validation_notes"] = " · ".join(dict.fromkeys(notes))[:1000]
    return field


_TYPE_FRIENDLY = {
    "VARCHAR2": "Character", "NVARCHAR2": "Character", "CHAR": "Character",
    "CLOB": "Character", "LONG": "Character",
    "NUMBER": "Number", "FLOAT": "Number", "INTEGER": "Number",
    "DATE": "Date", "TIMESTAMP": "Date",
}


def _friendly_type(oracle_type: str) -> str:
    """Oracle's type name -> the vocabulary the validator already speaks."""
    return _TYPE_FRIENDLY.get(str(oracle_type or "").upper(), "Character")
