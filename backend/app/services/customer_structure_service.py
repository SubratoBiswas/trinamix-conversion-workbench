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
        cols = list(fr.columns)
        touched: list[str] = []
        rows = len(fr)
        rref = ref[:rows] if len(ref) >= rows else ref + [f"{i + 100000}" for i in range(len(ref), rows)]

        def setcol(col: str | None, value: Any):
            if col is not None:
                fr[col] = value
                touched.append(col)

        # Batch id everywhere it exists.
        setcol(_find(cols, "batch"), batch_id)

        # Party level key.
        setcol(_find(cols, "party", "original", "system", "reference"), rref)
        po = _find(cols, "party", "original", "system")
        # guard: "...reference" also contains "system"; only set the bare OS column
        if po and _norm(po) == _norm("Party Original System"):
            setcol(po, source_system)

        # Customer Account level.
        ca_ref = _find(cols, "customer", "account", "source", "system", "reference")
        ca_os = _find(cols, "customer", "account", "source", "system")
        if ca_os and _norm(ca_os) == _norm("Customer Account Source System"):
            if level == "party":
                setcol(ca_os, _ACCOUNT_SENTINEL)
                setcol(ca_ref, _ACCOUNT_SENTINEL)
            else:
                setcol(ca_os, source_system)
                setcol(ca_ref, rref)

        # Account Site level.
        as_ref = _find(cols, "account", "site", "source", "system", "reference")
        as_os = _find(cols, "account", "site", "source", "system")
        if as_os and _norm(as_os) == _norm("Account Site Source System"):
            if level == "site":
                setcol(as_os, source_system)
                setcol(as_ref, rref)
            else:
                # party/account level → leave the site columns blank (Oracle rule).
                setcol(as_os, "")
                setcol(as_ref, "")

        if touched:
            report[name] = {"rows": rows, "filled": sorted(set(touched))}

    return report
