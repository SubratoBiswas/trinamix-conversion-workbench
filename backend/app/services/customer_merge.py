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
# Each row's OWN internalid, carried verbatim from the source (like ENTITYID_COL).
# On a master/party row this IS the customer's internalid; on a child row it is the
# address/contact record's own internalid, which is NOT what a party link wants.
INTERNALID_COL = "__internalid"
# The CUSTOMER's internalid, resolved by entityid from the master rows and stamped on
# EVERY row (party and children). This is what Party Original System Reference must
# carry so a customer's party, addresses and contacts all point at the same key
# (REC-04). Falls back to the entityid for a customer that has no master row.
PARTYREF_COL = "__partyref"

# The three source grains a Customer load is built from.
PARTY = "party"      # the customer master — one row per customer
SITE = "site"        # address / location files — many rows per customer
CONTACT = "contact"  # contact file — many rows per customer


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


def _find_col_ci(cols, name: str) -> Optional[str]:
    """First column whose normalised name matches ``name`` (case/punct-insensitive)."""
    want = _norm(name)
    for c in cols:
        if _norm(c) == want:
            return c
    return None


def _is_blank_series(s: "pd.Series") -> "pd.Series":
    t = s.astype(str).str.strip()
    return t.eq("") | t.str.lower().isin(["nan", "none", "null", "na", "<na>"])


# ── Cross-grain enrichment by entityid ──────────────────────────────────────
# A Customer load is several source FILES at different grains, all keyed by
# ``entityid``, and each converted on its own. So a column lives only on the file
# that carries it: person names are in the CONTACT file, companyname/startdate/
# datecreated in the MASTER, addresses in the ADDRESS files. But rules and mappings
# need them on a DIFFERENT grain than they live on:
#   * Party Type ("PERSON if a name is present and companyname is blank, else
#     ORGANIZATION") and the Person Name fields are evaluated on the MASTER/party rows,
#     yet firstname/middlename/lastname are only in the contact file;
#   * Party Site From Date = COALESCE(startdate, datecreated) is on the ADDRESS/site
#     rows, yet startdate/datecreated are only in the master.
# Because every file shares entityid, we JOIN these few columns across the files by
# entityid BEFORE conversion, so each source frame carries what its rules read. Only a
# column the frame LACKS (or has entirely blank) is filled — real source data is never
# overwritten. This is what makes Party Type / Person names / Party Site From Date
# reflect in the output (REC-05 / REC-07 / REC-08).
BORROWABLE_SRC_COLS = (
    "firstname", "middlename", "lastname",
    "companyname", "startdate", "datecreated",
)


def build_entity_enrichment(frames) -> dict:
    """``{borrowable col -> {entityid -> first non-blank value}}`` across the raw
    customer source frames. First non-blank wins (sources arrive master-first)."""
    enr: dict[str, dict] = {}
    for f in frames or []:
        if f is None or len(getattr(f, "columns", [])) == 0 or len(f) == 0:
            continue
        ecol = _find_col_ci(f.columns, "entityid")
        if not ecol:
            continue
        eids = f[ecol].astype(str).str.strip()
        for want in BORROWABLE_SRC_COLS:
            col = _find_col_ci(f.columns, want)
            if not col:
                continue
            vals = f[col].astype(str).str.strip()
            d = enr.setdefault(want, {})
            for e, v in zip(eids.tolist(), vals.tolist()):
                if not e:
                    continue
                if v and v.lower() not in ("nan", "none", "null") and e not in d:
                    d[e] = v
    return enr


def enrich_source_frame(src, enrichment: dict):
    """Add the borrowable columns to a RAW source frame from the cross-source
    ``enrichment`` (keyed by entityid). A column the frame already carries with real
    values is left untouched; only an absent or entirely-blank one is filled, so a
    rule/mapping that reads it stops seeing blanks. Returns the frame (copied only if
    changed)."""
    if src is None or not enrichment:
        return src
    ecol = _find_col_ci(src.columns, "entityid")
    if not ecol:
        return src
    eids = src[ecol].astype(str).str.strip()
    changed = False
    for col, mapping in enrichment.items():
        if not mapping:
            continue
        existing = _find_col_ci(src.columns, col)
        if existing is not None and not bool(_is_blank_series(src[existing]).all()):
            continue                      # real values already present — leave it
        if not changed:
            src = src.copy()
            changed = True
        target = existing or col
        src[target] = eids.map(lambda e: mapping.get(e, ""))
    return src


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


# ── Which grain a SOURCE carries ────────────────────────────────────────────
# Decided from the RAW SOURCE columns, not the converted frame. The converted frame
# is the wrong place to look: the structural glue fills reference columns (Party Site
# Original System Reference, Location Original System Reference, account references)
# on EVERY row of EVERY source, so a "site"/"party" anchor matched on the converted
# frame scores 1.0 for all four files and the grains collapse to one. The source
# columns are unambiguous — the master has `companyname`, the address files have
# `addr*`/`city`/`zip`, the contact file has `firstname`/`lastname` — and carry no
# generated linkage. Matched loosely (normalised substring) so a NetSuite `addr1`
# and a friendlier `Address 1` both read as an address.

# Contact FIRST: a contact extract can also carry an e-mail-ish column, but only a
# contact file has person names — so the presence of a person name is decisive.
_CONTACT_SRC = ("firstname", "lastname", "fullname", "contactname", "middlename",
                "givenname", "surname")
# Address / site: a real address-BLOCK column. Deliberately specific — a bare
# "address" substring also matches a customer master's one-off DFF columns
# (custentity_dsg_consignee_address …), which put the whole master on the address
# sheets. A numbered address line, an address-line label, or a city do not appear on
# the master, so they identify an address extract cleanly.
_SITE_SRC = ("addr1", "addr2", "addr3", "addr4",
             "address1", "address2", "address3", "address4",
             "addressline", "streetaddress", "addressee", "addresslabel", "city")
# Party / account: a company / customer identity column. Deliberately NOT "name"
# alone (too broad) and NOT account-number/reference (the glue fills those).
_PARTY_SRC = ("companyname", "company", "organizationname", "orgname",
              "legalname", "customername", "partyname")


def classify_source_columns(cols) -> Optional[str]:
    """The grain a source carries, from its raw column names. Contact (person names)
    wins first, then an address-block column, then a company/customer identity;
    otherwise None (left unclassified, never filtered out on a guess)."""
    names = {_norm(c) for c in (cols or [])}
    if not names:
        return None

    def has_sub(subs):
        return any(any(s in n for s in subs) for n in names)

    if has_sub(_CONTACT_SRC):
        return CONTACT
    if has_sub(_SITE_SRC):
        return SITE
    if has_sub(_PARTY_SRC):
        return PARTY
    return None


# Fallback anchors on the CONVERTED frame, used only when the source columns are
# unavailable. Kept narrow — the real DATA columns a source fills, never the glue's
# reference columns — so they do not all light up at once the way the broad set did.
_PARTY_ANCHORS = ("organizationname",)
_SITE_ANCHORS = ("addressline1", "addressline2", "addressee", "addressline")
_CONTACT_ANCHORS = ("personfirstname", "personlastname")


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


# ── The customer's internalid, resolved by entityid ─────────────────────────
def set_party_ref_from_master(df: "pd.DataFrame") -> "pd.DataFrame":
    """Stamp ``PARTYREF_COL`` = the CUSTOMER's internalid on every row, resolved from
    the master/party-grain rows by ``entityid`` (REC-04).

    Every source carries an ``internalid``, but it is the record's OWN id — a
    customer, each of its addresses and each of its contacts have different ones.
    The Party Original System Reference must be the CUSTOMER's internalid, the same
    on the party row and all its children, or the child points at a party that does
    not exist. So we take the internalid from the master rows only, key it by
    ``entityid``, and map it onto every row. A customer with no master row (or a row
    with no key) falls back to its ``entityid`` so the link is still internally
    consistent. No-op unless the frame carries the grain + entityid + internalid the
    merge threads through."""
    if df is None or GRAIN_COL not in getattr(df, "columns", []):
        return df
    if ENTITYID_COL not in df.columns or INTERNALID_COL not in df.columns:
        return df
    g = df[GRAIN_COL].astype(str).str.strip().str.lower()
    eid = df[ENTITYID_COL].astype(str).str.strip()
    iid = df[INTERNALID_COL].astype(str).str.strip()
    master = g.eq(PARTY)
    id_by_entity: dict[str, str] = {}
    for e, i in zip(eid[master].tolist(), iid[master].tolist()):
        if not e or e in id_by_entity:
            continue
        if i and i.lower() not in ("nan", "none", "null"):
            id_by_entity[e] = i
    df = df.copy()
    df[PARTYREF_COL] = [id_by_entity.get(e, e) for e in eid.tolist()]
    return df


def _party_link_series(sub: "pd.DataFrame"):
    """The value a row uses for its Party Original System Reference — the customer's
    internalid (``PARTYREF_COL``) when the REC-04 resolve has run, else the customer
    key ``entityid`` (the previous behaviour)."""
    if PARTYREF_COL in sub.columns:
        return sub[PARTYREF_COL].astype(str).str.strip()
    if ENTITYID_COL in sub.columns:
        return sub[ENTITYID_COL].astype(str).str.strip()
    return None


# ── The per-sheet reshape ───────────────────────────────────────────────────
def _set_party_link(sub: pd.DataFrame) -> None:
    """Point every row's PARTY link at its customer, in place.

    Party Original System Reference is the column a child row uses to say which party
    it belongs to. In these extracts it is mapped to the record's OWN ``internalid``
    — different for a customer, its addresses and its contacts — so a child pointed
    at a party that did not exist. Overwrite it with the CUSTOMER's internalid
    (``PARTYREF_COL``, resolved from the master by entityid — REC-04), falling back to
    the customer key ``entityid`` when the resolve has not run, so the child resolves
    to the one deduped party row. The site-level keys (…Site Source System Reference,
    already the unique ``entityid_internalid``) are left untouched — only the party
    link is retargeted."""
    key = _party_link_series(sub)
    if key is None:
        return
    keyv = key.tolist()
    for c in sub.columns:
        nc = _norm(c)
        if "site" in nc:
            continue
        if all(w in nc for w in ("party", "original", "system", "reference")):
            # Only where we actually have a key; keep any existing value on a
            # key-less row rather than blanking it.
            sub[c] = [e if e else v for e, v in zip(keyv, sub[c].astype(str))]


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
    """The customer linkage reference for each row of a reshaped sheet — the
    customer's internalid (``PARTYREF_COL``, REC-04) when resolved, else the row's
    ``entityid``. Fed to the structural glue as the Party/Account Source System
    Reference so every child row points at its customer's party AND carries the same
    key the party row does. None when the frame does not carry the key (non-customer
    or single-source paths)."""
    if sub is None:
        return None
    key = _party_link_series(sub)
    return key.tolist() if key is not None else None
