"""Row access + result interpolation + branch conditions — the shared helpers the
stateful rule strategies (CONCAT, CASE_WHEN, CONDITIONAL, COALESCE, SUFFIX/PREFIX,
SELF_LOOKUP) read. Moved VERBATIM out of engine._apply_one_rule's neighbourhood so the
domain owns them; engine imports them back under their historical underscore names, so
its remaining branches are unchanged. Pure: only re + the domain text helpers."""
from __future__ import annotations

import re
from typing import Any

from app.domain.text import (
    to_str as _to_str, to_float as _to_float, is_blank as _is_blank,
    TRUEISH as _TRUEISH, FALSEISH as _FALSEISH,
)


_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _interpolate(template: Any, row: Any) -> Any:
    """Substitute ``{Column}`` tokens in a result string with the row's values.

    Reported 05-Aug: a CASE/WHEN branch set to ``E{Employee_ID}`` shipped the
    literal text ``E{Employee_ID}`` — the live preview showed it, and so did the
    file. The analyst means "an E, then this row's Employee_ID": the only way to
    express "prefix the id with a letter that depends on Worker Type" in one rule.
    The engine returned every ``then`` verbatim, so no result value could reference
    another column.

    A token is a column NAME (matched case- and punctuation-loosely, the same way
    the rest of the engine resolves columns), replaced by that column's cell value.
    A token that names no column on the row is left EXACTLY as written — including
    its braces — so a result that legitimately contains braces, or names a column
    this extract does not have, is never silently blanked. Non-strings and rows
    without a lookup pass straight through.
    """
    if row is None or not isinstance(template, str) or "{" not in template:
        return template
    # Resolve each column name once, tolerant of case/spacing/punctuation, because
    # a frame header ("EMPLOYEE_ID") and what the analyst typed ("Employee_ID" or
    # "employee id") routinely differ — the same mismatch _resolve_column handles.
    def _lookup(name: str):
        if row is None:
            return None
        try:
            if name in row:
                return row.get(name)
        except Exception:  # noqa: BLE001 — row may not support `in`
            pass
        want = re.sub(r"[^a-z0-9]", "", name.lower())
        keys = None
        for attr in ("keys", "_keys"):
            fn = getattr(row, attr, None)
            if callable(fn):
                try:
                    keys = list(fn())
                    break
                except Exception:  # noqa: BLE001
                    keys = None
        for k in (keys or []):
            if re.sub(r"[^a-z0-9]", "", str(k).lower()) == want:
                return row.get(k)
        return None

    def _sub(match: "re.Match") -> str:
        raw = match.group(1).strip()
        # Optional modifier: ``{Column|digits}`` inserts only the DIGITS of the cell.
        # Needed for AssignmentNumber: a contingent worker's Employee_ID already
        # carries a letter prefix (C12345), so "C{Employee_ID}" doubled it to CC12345 —
        # the analyst wants "C" + the digits (C12345). Employees (numeric ids) are
        # unaffected. Extensible: |alnum keeps letters+digits, |upper/|lower fold case.
        mod = ""
        name = raw
        if "|" in raw:
            name, mod = raw.split("|", 1)
            name, mod = name.strip(), mod.strip().lower()
        val = _lookup(name)
        if val is None or _to_str(val).strip() == "":
            # Unknown column -> leave the token untouched (do not blank a result).
            # A resolved-but-empty cell -> empty string, so "E{Employee_ID}" on a
            # row with no id becomes "E", which is the sensible reading.
            try:
                if row is not None and (name in row):
                    return ""
            except Exception:  # noqa: BLE001
                pass
            if _lookup(name) is not None:
                return ""
            return match.group(0)
        s = _to_str(val)
        if mod == "digits":
            s = re.sub(r"\D", "", s)
        elif mod == "alnum":
            s = re.sub(r"[^A-Za-z0-9]", "", s)
        elif mod == "upper":
            s = s.upper()
        elif mod == "lower":
            s = s.lower()
        return s

    return _PLACEHOLDER.sub(_sub, template)


_COMPARISON_OPS = {
    "eq": lambda a, b: _to_str(a) == _to_str(b),
    "neq": lambda a, b: _to_str(a) != _to_str(b),
    "gt": lambda a, b: (_to_float(a) or 0) > (_to_float(b) or 0),
    "gte": lambda a, b: (_to_float(a) or 0) >= (_to_float(b) or 0),
    "lt": lambda a, b: (_to_float(a) or 0) < (_to_float(b) or 0),
    "lte": lambda a, b: (_to_float(a) or 0) <= (_to_float(b) or 0),
    "in": lambda a, b: _to_str(a) in (b if isinstance(b, (list, tuple)) else _to_str(b).split(",")),
    "notin": lambda a, b: _to_str(a) not in (b if isinstance(b, (list, tuple)) else _to_str(b).split(",")),
    "contains": lambda a, b: _to_str(b) in _to_str(a),
    "startswith": lambda a, b: _to_str(a).startswith(_to_str(b)),
    "endswith": lambda a, b: _to_str(a).endswith(_to_str(b)),
    "regex": lambda a, b: re.search(_to_str(b), _to_str(a)) is not None,
    "isblank": lambda a, _b: _is_blank(a),
    "notblank": lambda a, _b: not _is_blank(a),
    # A boolean-ish column is TRUE/FALSE, not present/absent, and reading one with
    # `notblank` inverts the rule on almost every row. Tax Organization Type is the
    # proof: its branch was {"if_column": "Is Individual", "op": "notblank"}, and in
    # the NetSuite extract that column reads "No" on 6,985 of 7,495 rows and "Yes" on
    # 437. "No" is not blank, so 7,422 suppliers — 99% of them, including "3D Hubs
    # Manufacturing LLC" and "A.B Boyd Co" — were loaded as INDIVIDUAL, and only the
    # 73 rows where the column was EMPTY came out CORPORATION. Exactly backwards.
    #
    # Comparison is trimmed and case-insensitive, and the vocabulary is the one
    # MAP_BOOLEAN already uses, so the two cannot disagree about what "Y" means.
    "istrue": lambda a, _b: _to_str(a).strip().lower() in _TRUEISH,
    "isfalse": lambda a, _b: _to_str(a).strip().lower() in _FALSEISH,
}


def _resolve_column(spec: Any, row: Any) -> Any:
    """The first of ``spec``'s candidate spellings this row actually has.

    A rule column may be written as one name or as a LIST of candidate spellings.
    The reason is in customer_rules_nextpower.json: rules are dictated in prose
    ("entityid + _ + internalid") and the extract's real headers are whatever the
    legacy system exported, so a single guessed spelling binds to nothing and
    fails silently in a file that looks correct. Naming several costs nothing.

    Returns the name to read, or None when the row has none of them — in which
    case the caller reads a blank, which is the same thing an absent column has
    always meant here. Preference order is the order written: a spelling the row
    HAS but leaves blank still beats one it does not have at all, so a genuinely
    empty cell is not silently replaced by a different column's value.
    """
    if not isinstance(spec, (list, tuple)):
        return spec
    names = [c for c in spec if str(c or "").strip()]
    if row is None:
        return names[0] if names else None
    present = [c for c in names if c in row]
    for c in present:
        if not _is_blank(row.get(c)):
            return c
    return present[0] if present else (names[0] if names else None)


def _row_value_ci(row: Any, name: Any) -> Any:
    """This row's value for ``name``, resolving the column case/space-insensitively.

    ``_resolve_column`` returns a single-string spec verbatim, so a config spelling
    ("Parent Vendor Id") that differs from the extract's raw header (parent_vendor_id)
    read a blank — the reason SELF_LOOKUP (Parent Supplier) returned empty on every
    row (PROC-01 Gap B). Try the resolved name, then fall back to a normalised match
    over the row's own keys. Works for a dict or a pandas Series (both expose keys())."""
    if row is None:
        return ""
    col = _resolve_column(name, row)
    if col is not None and col in row:
        return row.get(col)
    target = re.sub(r"[^a-z0-9]", "", str(col if col is not None else name).lower())
    try:
        for k in (row.keys() if hasattr(row, "keys") else row):
            if re.sub(r"[^a-z0-9]", "", str(k).lower()) == target:
                return row.get(k)
    except Exception:  # noqa: BLE001
        pass
    return ""


def _branch_holds(br: dict, value: Any, row: Any) -> bool:
    """Does one CASE_WHEN / SUFFIX_WHEN branch fire?

    Two shapes. A plain branch names one column, one op and one value. A branch
    carrying ``all`` is a CONJUNCTION of those — every clause must hold — which
    the analyst's Party Type rule needs and the single-clause form cannot say:
    "if organization name is blank AND a person name is not blank". Written as
    three separate branches instead, the first one to match wins and the
    organization-name test is never reached.
    """
    clauses = br.get("all")
    if isinstance(clauses, (list, tuple)) and clauses:
        return all(_branch_holds(c, value, row) for c in clauses)
    clauses = br.get("any")
    if isinstance(clauses, (list, tuple)) and clauses:
        return any(_branch_holds(c, value, row) for c in clauses)
    cmp = _COMPARISON_OPS.get((br.get("op") or "eq").lower())
    if not cmp:
        return False
    col = _resolve_column(br.get("if_column"), row)
    left = row.get(col) if (col and row is not None) else value
    try:
        return bool(cmp(left, br.get("value")))
    except Exception:                                           # noqa: BLE001
        return False
