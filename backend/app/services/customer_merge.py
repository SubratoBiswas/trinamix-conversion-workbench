"""Correct multi-source Customer merge — grain-aware sheet rows + entityid linkage.

A Customer load arrives as several source files that describe the SAME customers at
different grains — a customer master (one row per customer), shipping/billing address
files (many rows per customer), and a contact file (many rows per customer). Every
one of them carries the customer's business key ``entityid`` (e.g. ``NT-2437``): the
master as the customer's own id, each address/contact row as the id of the customer
it belongs to.

The old merge stacked every source's rows into ONE frame and replicated that frame
across all 19 Oracle interface sheets, linking parent to child BY ROW POSITION. The
result: every address and every contact became its own standalone party, so each of
the 15 interface files carried the full 12,409 + 9,809 + 3,770 + 5,523 = 31,511 rows,
there were ~26k blank/duplicate party rows, and a child row's Party Original System
Reference was its own record id — pointing at a party that did not exist.

This module fixes the row GRAIN and the LINKAGE:

  * Each interface sheet is given only the rows of the source grain it represents —
    the party/account/profile sheets get the customer master, the site/location
    sheets get the address files, the contact sheets get the contact file.
  * The party/account sheets are de-duplicated to one row per ``entityid``.
  * Every sheet is linked by ``entityid``, so a shipping address for ``NT-2437``
    carries Party Original System Reference = ``NT-2437`` and resolves to the single
    deduped party row for that customer.

Everything here is pure and defensive: an unrecognised sheet or a frame with no
grain signal is returned untouched (the previous behaviour), because shipping rows
un-reshaped is recoverable and dropping real rows is not.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd

# The hidden columns the merge threads through the wide frame. Dropped by the
# per-sheet reindex before anything is written, so they never reach the file.
GRAIN_COL = "__grain"
ENTITYID_COL = "__entityid"

# The three source grains a Customer load is built from.
PARTY = "party"      # the customer master — one row per customer
SITE = "site"        # address / location files — many rows per customer
CONTACT = "contact"  # contact file — many rows per customer


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


# ── Which grain an interface SHEET belongs to ───────────────────────────────
# Matched on the normalised sheet name. Order matters: "contact" is tested before
# "site"/"account" so HZ_IMP_ACCTCONTACTS_T lands as contact, and "site" before the
# party/account catch so HZ_IMP_ACCTSITES_T lands as site. Anything the map does not
# recognise returns None and keeps every row (no reshape) — a safe passthrough.
def sheet_grain(sheet_name: Optional[str]) -> Optional[str]:
    n = _norm(sheet_name)
    if not n:
        return None
    # Contact grain: contacts, contact points/roles, role-responsibility, person
    # language — all one row per contact person.
    if ("contact" in n or "personlang" in n or "roleresp" in n
            or "personprofile" in n):
        return CONTACT
    # Site / address grain: party sites, account sites, their uses, and locations.
    if "site" in n or "location" in n:
        return SITE
    # Party / account grain: parties, accounts, customer profiles, relationships,
    # classifications — one row per customer.
    if ("parties" in n or "account" in n or "profile" in n or "relship" in n
            or "classific" in n):
        return PARTY
    return None


# ── Which grain a SOURCE frame carries ──────────────────────────────────────
# Decided from which anchor target columns the converted frame actually populates:
# a customer master fills Party/Organization Name, an address file fills the address
# lines, a contact file fills the person name. The frame is keyed by target FIELD
# name at this point (the Oracle header rename happens later), so the anchors are
# matched loosely against those names.
_PARTY_ANCHORS = ("partyname", "organizationname", "accountname", "accountnumber")
# Address anchors, broad on purpose: the customer master carries none of city /
# postal / address-line, so any of them signals an address (site) source even when
# the address-line mapping itself is sparse.
_SITE_ANCHORS = ("addressline", "addressee", "partysite", "locationorig",
                 "city", "postalcode", "postcode", "county")
_CONTACT_ANCHORS = ("personfirstname", "personlastname", "personname",
                    "contactfirstname", "contactlastname")


def _nonblank_fraction(frame: pd.DataFrame, anchors: tuple[str, ...]) -> float:
    """Largest non-blank fraction among columns whose name matches an anchor."""
    best = 0.0
    n = len(frame)
    if n == 0:
        return 0.0
    for c in frame.columns:
        nc = _norm(c)
        if any(a in nc for a in anchors):
            s = frame[c].astype(str).str.strip()
            frac = float((s.ne("") & ~s.str.lower().isin(["nan", "none", "null"])).mean())
            if frac > best:
                best = frac
    return best


def classify_frame_grain(frame: pd.DataFrame) -> Optional[str]:
    """The grain a converted source frame carries, or None when it is unclear.

    Returns whichever of party/site/contact has the strongest anchor signal, as long
    as it clears a small floor — so a frame that populates none of the anchors (an
    unexpected source shape) is left unclassified and, downstream, never filtered
    OUT of a sheet on a guess."""
    if frame is None or len(frame) == 0:
        return None
    scores = {
        PARTY: _nonblank_fraction(frame, _PARTY_ANCHORS),
        SITE: _nonblank_fraction(frame, _SITE_ANCHORS),
        CONTACT: _nonblank_fraction(frame, _CONTACT_ANCHORS),
    }
    grain, score = max(scores.items(), key=lambda kv: kv[1])
    return grain if score >= 0.10 else None


# ── The per-sheet reshape ───────────────────────────────────────────────────
def _set_party_link(sub: pd.DataFrame) -> None:
    """Point every row's PARTY link at its customer, in place.

    Party Original System Reference is the column a child row uses to say which party
    it belongs to. In these extracts it is mapped to the record's OWN ``internalid``
    — different for a customer, its addresses and its contacts — so a child pointed
    at a party that did not exist. Overwrite it with the customer key ``entityid`` so
    the child resolves to the one deduped party row. The site-level keys (…Site
    Source System Reference, already the unique ``entityid_internalid``) are left
    untouched — only the party link is retargeted."""
    if ENTITYID_COL not in sub.columns:
        return
    eid = sub[ENTITYID_COL].astype(str).str.strip()
    for c in sub.columns:
        nc = _norm(c)
        if "site" in nc:
            continue
        if all(w in nc for w in ("party", "original", "system", "reference")):
            # Only where we actually have a key; keep any existing value on a
            # key-less row rather than blanking it.
            sub[c] = [e if e else v for e, v in zip(eid, sub[c].astype(str))]


def sheet_rows(df: pd.DataFrame, sheet_name: Optional[str]) -> pd.DataFrame:
    """The subset of the merged frame that belongs on ``sheet_name``.

    Party/account sheets get the party-grain rows, de-duplicated to one per
    ``entityid``; site and contact sheets get their own grain's rows unchanged. Every
    returned row's party link is retargeted to its customer key. A sheet whose grain
    is unknown, or whose grain has no rows in this load, is given the whole frame back
    (the previous behaviour) rather than an empty sheet — an empty backbone sheet
    would break the load, and a slightly over-full one will not.
    """
    if df is None or GRAIN_COL not in df.columns or len(df) == 0:
        return df
    grain = sheet_grain(sheet_name)
    if grain is None:
        return df
    # Case/space-insensitive so a client cleansing profile that touches the tag can
    # never silently turn the reshape off (grain constants are lower-case).
    g = df[GRAIN_COL].astype(str).str.strip().str.lower()
    sub = df[g == grain]
    if len(sub) == 0:
        # No source of this grain in the load — don't empty the sheet.
        return df
    if grain == PARTY and ENTITYID_COL in sub.columns:
        key = sub[ENTITYID_COL].astype(str).str.strip()
        # Keep blank-key rows as-is (nothing to collapse them on); collapse the rest
        # to one row per customer, first occurrence winning (sources arrive in load
        # order, master first).
        keyed = sub[key.ne("")].drop_duplicates(subset=[ENTITYID_COL], keep="first")
        blank = sub[key.eq("")]
        sub = pd.concat([keyed, blank]) if len(blank) else keyed
    sub = sub.reset_index(drop=True)
    _set_party_link(sub)
    return sub


def sheet_reference(sub: pd.DataFrame) -> Optional[list]:
    """The customer linkage reference for each row of a reshaped sheet — the row's
    ``entityid``. Fed to the structural glue as the Party/Account Source System
    Reference so every child row points at its customer's party. None when the
    frame does not carry the key (non-customer or single-source paths)."""
    if sub is None or ENTITYID_COL not in getattr(sub, "columns", []):
        return None
    return sub[ENTITYID_COL].astype(str).str.strip().tolist()
