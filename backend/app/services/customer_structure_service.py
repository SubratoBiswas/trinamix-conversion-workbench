"""Customer Import structural glue — the linkage columns Oracle needs but no source
system provides.

Oracle's Customer Import is 19 interface tables (HZ_IMP_PARTIES_T → HZ_IMP_ACCOUNTS_T
→ HZ_IMP_ACCTSITES_T … + RA_CUSTOMER_PROFILES_INT_ALL). They're stitched together
not by internal IDs but by two columns that repeat down the hierarchy — the Source
System (``ORIG_SYSTEM`` / "… Original System") and the Source System Reference
("… Original System Reference"). A child points at its parent by carrying the
parent's system + reference. None of that exists in a flat customer extract, so the
tool has to generate it, exactly like it already does for the Supplier fan-out.

This pass fills, on every generated interface sheet that has the columns:

  * Batch Identifier            — one batch number for the whole load
  * Party Original System (+Ref)         — the party-level key
  * Customer Account Source System (+Ref) — the account-level key
  * Account Site Source System (+Ref)     — the site-level key

and applies Oracle's documented sentinel rule BY LEVEL (from the template's own
instructions):

  * party level  → Customer Account Source System/Ref = "-1", Account Site blank
  * account level → Customer Account set,               Account Site blank
  * site level   → both set

The reference is a per-customer stable key: the source's own account/party number
if present, else a synthesized running key, so parent and child rows agree.

SCOPE, stated honestly: this generates the linkage for an ACCOUNT-LEVEL customer
load (one party + one account + profile per source row). Fanning a customer into
multiple party-site / account-site / site-use child rows, and final validation
against a specific Fusion instance, is the remaining work — this is the structural
foundation, not the whole model.
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd

# Oracle's default sentinel for "no customer account at this level".
_ACCOUNT_SENTINEL = "-1"


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


# Column roles, matched loosely against each sheet's headers (which carry the
# friendly Oracle labels, e.g. "Party Original System Reference").
def _find(cols: list[str], *needles: str) -> str | None:
    for c in cols:
        n = _norm(c)
        if all(_norm(x) in n for x in needles):
            return c
    return None


def _reference_series(frame: pd.DataFrame, n_rows: int, source_system: str) -> list[str]:
    """A stable per-row reference key. Prefer a real source key column; else
    synthesize a running one so parent/child rows still line up."""
    cols = list(frame.columns)
    for label in (("account", "number"), ("party", "number"), ("customer", "number")):
        col = _find(cols, *label)
        if col is not None:
            vals = frame[col].astype(str).str.strip().tolist()
            if any(v and v.lower() not in ("nan", "none", "") for v in vals):
                return [v if v and v.lower() not in ("nan", "none") else f"{i + 100000}"
                        for i, v in enumerate(vals)]
    return [f"{i + 100000}" for i in range(n_rows)]


def apply_customer_structure(
    sheet_frames: dict[str, pd.DataFrame],
    *,
    source_system: str,
    batch_id: str,
    level: str = "account",
) -> dict:
    """Fill the linkage + sentinel columns across the customer interface sheets,
    in place. Returns a small report of what was populated."""
    # A single reference series shared across every sheet, keyed by the party
    # sheet's own numbers where available (all sheets share row order because they
    # derive from the same transformed frame).
    party_frame = None
    for name, fr in sheet_frames.items():
        if "parties" in _norm(name):
            party_frame = fr
            break
    n_rows = max((len(fr) for fr in sheet_frames.values()), default=0)
    ref = _reference_series(party_frame if party_frame is not None else
                            (next(iter(sheet_frames.values())) if sheet_frames else pd.DataFrame()),
                            n_rows, source_system)

    report: dict[str, dict] = {}
    for name, fr in sheet_frames.items():
        touched = apply_to_frame(fr, source_system=source_system, batch_id=batch_id,
                                 ref=ref, level=level, sheet_name=name)
        if touched:
            report[name] = {"rows": len(fr), "filled": touched}
    return report


def reference_series(frame: "pd.DataFrame", n_rows: int, source_system: str) -> list[str]:
    """Public wrapper: the per-row linkage reference derived once from the party
    frame, reused by every sheet so parent and child rows agree. Used by the
    streaming generator, which finalizes one sheet at a time to bound memory."""
    return _reference_series(frame, n_rows, source_system)


def level_for_sheet(sheet_name: str | None) -> str | None:
    """Which linkage level an interface sheet sits at, or None when unknown.

    The site-level sheets are the reason this exists. ``apply_to_frame`` was called
    with a hardcoded ``level="account"`` for EVERY sheet, and the account branch
    writes Account Site Source System and its Reference to EMPTY STRING. So on
    HZ_IMP_ACCTSITES_T and HZ_IMP_ACCTSITEUSES_T — the two sheets where the site key
    is the whole point — it was blanked on every row. That is CW_Issues 31-Jul
    verbatim: "Account Site Source System: NETSUITE (default value) -> appears blank
    in the output file."

    Only the two sheets whose level is not in doubt are named. Everything else
    returns None and keeps the caller's level, because inventing a party-level
    sentinel on sheets nobody has reported would be changing behaviour on a guess.
    """
    key = _norm(sheet_name)
    if not key:
        return None
    if key in ("hzimpacctsitest", "hzimpacctsiteusest"):
        return "site"
    return None


def apply_to_frame(
    fr: "pd.DataFrame", *, source_system: str, batch_id: str,
    ref: list[str], level: str = "account",
    sheet_name: str | None = None, protected: set[str] | None = None,
) -> list[str]:
    """Fill the linkage + sentinel columns on ONE interface frame, in place, using
    a shared reference. Returns the columns touched. Splitting this out lets the
    output writer process sheets one at a time instead of holding all 19 in memory
    (which OOM'd the worker on a large Customer load).

    ``protected`` is the set of columns THE ANALYST HAS DECIDED — a default they
    typed, a source column they approved, or a Keep blank they pressed. This pass
    used to overwrite all of them unconditionally, which is the single cause behind
    four separate 31-Jul issues: Batch Identifier came back after Keep blank, and
    Customer Account Source System, Party Original System and Account Site Source
    System all ignored a NETSUITE default that the UI and the mapping sheet both
    showed correctly. Generated glue is a FALLBACK for what no source supplies; it
    was behaving as an override.

    ``sheet_name`` lets the level be derived per sheet instead of assumed.
    """
    cols = list(fr.columns)
    touched: list[str] = []
    rows = len(fr)
    rref = ref[:rows] if len(ref) >= rows else ref + [f"{i + 100000}" for i in range(len(ref), rows)]
    level = level_for_sheet(sheet_name) or level
    guarded = {_norm(c) for c in (protected or set())}

    def setcol(col: str | None, value: Any):
        if col is None:
            return
        if _norm(col) in guarded:
            # The analyst has spoken about this column on this sheet. Their value
            # is the deliverable; the glue exists for the columns they have not
            # filled in.
            return
        fr[col] = value
        touched.append(col)

    setcol(_find(cols, "batch"), batch_id)

    setcol(_find(cols, "party", "original", "system", "reference"), rref)
    po = _find(cols, "party", "original", "system")
    if po and _norm(po) == _norm("Party Original System"):
        setcol(po, source_system)

    ca_ref = _find(cols, "customer", "account", "source", "system", "reference")
    ca_os = _find(cols, "customer", "account", "source", "system")
    if ca_os and _norm(ca_os) == _norm("Customer Account Source System"):
        if level == "party":
            setcol(ca_os, _ACCOUNT_SENTINEL)
            setcol(ca_ref, _ACCOUNT_SENTINEL)
        else:
            setcol(ca_os, source_system)
            setcol(ca_ref, rref)

    as_ref = _find(cols, "account", "site", "source", "system", "reference")
    as_os = _find(cols, "account", "site", "source", "system")
    if as_os and _norm(as_os) == _norm("Account Site Source System"):
        if level == "site":
            setcol(as_os, source_system)
            setcol(as_ref, rref)
        else:
            setcol(as_os, "")
            setcol(as_ref, "")

    return sorted(set(touched))


# ---------------------------------------------------------------------------
# What the screen needs to know about all of the above
# ---------------------------------------------------------------------------
# Every column `apply_to_frame` fills, and a phrase describing where the value
# comes from. The grid reads this so a generated column stops being reported as
# "Required field with no source and no default" — which is not merely unhelpful
# but FALSE: the column ships populated.
#
# 05-Aug: Batch Identifier on HZ_IMP_PARTIES_T showed SOURCE (none), status
# Unmapped, a red REQUIRED chip and that note, while every row of the shipped file
# carried CONV-E3F9D5. An analyst reading that screen has no way to tell whether
# the tool is broken or the field is handled, and reasonably concludes the former.
#
# Keyed by the same loose needle-match `_find` uses, so this list and the code that
# fills the columns cannot drift into disagreeing about which columns are glue —
# `test_the_generated_roles_cover_every_column_the_glue_fills` asserts exactly that.
_GENERATED_ROLES: list[tuple[tuple[str, ...], str]] = [
    (("batch",), "batch identifier generated for this conversion"),
    (("party", "original", "system", "reference"), "party linkage reference generated"),
    (("party", "original", "system"), "party linkage source system generated"),
    (("customer", "account", "source", "system", "reference"),
     "account linkage reference generated"),
    (("customer", "account", "source", "system"),
     "account linkage source system generated"),
    (("account", "site", "source", "system", "reference"),
     "site linkage reference generated"),
    (("account", "site", "source", "system"), "site linkage source system generated"),
]


def generated_role(column: str) -> str | None:
    """Why this column is filled without a mapping, or None if it is not.

    Longest needle-set first, so "Party Original System Reference" is described as
    the reference and not as the system — the same precedence `_find` gets from the
    order of the calls in `apply_to_frame`.
    """
    n = _norm(column)
    if not n:
        return None
    for needles, why in _GENERATED_ROLES:
        if all(_norm(x) in n for x in needles):
            return why
    return None
