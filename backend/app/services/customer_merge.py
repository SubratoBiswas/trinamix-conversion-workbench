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
# overwritten. This is what makes Party Site From Date reflect on the site rows (REC-08).
#
# Person names and companyname are DELIBERATELY NOT borrowed. The contact people are
# now materialised as PERSON party rows on HZ_IMP_PARTIES_T in their own right (see
# sheet_rows), so their names come from the contact file directly — the grain they
# live on. Borrowing companyname onto the contact rows would give every contact its
# customer's company name and flip Party Type to ORGANIZATION; borrowing firstname onto
# the master rows would stamp a person's name onto the ORG party. Each grain keeps its
# own identity: master = companyname (ORGANIZATION), contact = names (PERSON). REC-05/07.
#
# ``title`` IS borrowed (REC-62). Job Title on HZ_IMP_CONTACTS_T must map to ``title``,
# but the contact extract carries no per-contact title — the only title in the load is
# on the customer master (its primary contact's job title, one per customer). Borrowing
# it by entityid puts that title on the customer's contact rows so Job Title populates;
# where the master has no title (it is ~99% blank) the contact's Job Title stays blank.
# This is a customer-level value applied to the customer's contacts, which is as fine a
# grain as the source data provides.
BORROWABLE_SRC_COLS = (
    "startdate", "datecreated", "title", "language",
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


def _party_link_series(sub: "pd.DataFrame", own_internalid: bool = False):
    """The value a row uses for its Party Original System Reference.

    On a CHILD sheet (a site/account row) it is the CUSTOMER's internalid
    (``PARTYREF_COL``, resolved from the master — REC-04), so the child points at its
    customer's org party. On the PARTIES sheet itself (``own_internalid=True``) it is
    the row's OWN internalid, because that sheet holds one row per party and each
    party's reference is its own key — the org master's internalid for an ORGANIZATION,
    the contact's internalid for a PERSON — so the contact people get unique party
    references instead of all sharing their customer's. Falls back to entityid when the
    resolve has not run (previous behaviour)."""
    if own_internalid and INTERNALID_COL in sub.columns:
        s = sub[INTERNALID_COL].astype(str).str.strip()
        # A row with no own internalid falls back to the customer ref, then entityid.
        if PARTYREF_COL in sub.columns:
            pr = sub[PARTYREF_COL].astype(str).str.strip()
            s = s.where(s.ne("") & ~s.str.lower().isin(["nan", "none"]), pr)
        return s
    if PARTYREF_COL in sub.columns:
        return sub[PARTYREF_COL].astype(str).str.strip()
    if ENTITYID_COL in sub.columns:
        return sub[ENTITYID_COL].astype(str).str.strip()
    return None


# ── The per-sheet reshape ───────────────────────────────────────────────────
def _set_party_link(sub: pd.DataFrame, own_internalid: bool = False) -> None:
    """Point every row's PARTY link at its customer, in place.

    Party Original System Reference is the column a child row uses to say which party
    it belongs to. In these extracts it is mapped to the record's OWN ``internalid``
    — different for a customer, its addresses and its contacts — so a child pointed
    at a party that did not exist. Overwrite it with the CUSTOMER's internalid
    (``PARTYREF_COL``, resolved from the master by entityid — REC-04), falling back to
    the customer key ``entityid`` when the resolve has not run, so the child resolves
    to the one deduped party row. ``own_internalid`` (the PARTIES sheet) instead uses
    each row's OWN internalid, so a contact PERSON party gets its own unique reference.
    The site-level keys (…Site Source System Reference, already the unique
    ``entityid_internalid``) are left untouched — only the party link is retargeted."""
    key = _party_link_series(sub, own_internalid=own_internalid)
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


def _dedupe_party_grain(sub: "pd.DataFrame") -> "pd.DataFrame":
    """One row per customer for the party grain — first occurrence (master) wins,
    blank-key rows kept as-is."""
    if ENTITYID_COL not in sub.columns:
        return sub
    key = sub[ENTITYID_COL].astype(str).str.strip()
    keyed = sub[key.ne("")].drop_duplicates(subset=[ENTITYID_COL], keep="first")
    blank = sub[key.eq("")]
    return pd.concat([keyed, blank]) if len(blank) else keyed


# ── First-per-customer flags (REC-09 / REC-23) ──────────────────────────────
# Oracle's Primary / Identifying flag marks exactly ONE site row per customer as
# the primary/identifying one. It cannot be expressed as a per-field transform
# rule: "Primary Indicator" is a single column NAME shared across four sheets
# (PARTYSITEUSES, ACCTSITEUSES, ACCTCONTACTS, CONTACTPTS), so the wide frame
# carries one value for all of them and the last mapping wins — the flag came out
# blank on the site sheets and could never differ per sheet. The reshape is the
# only place each sheet already has its own rows AND the entityid, so the flag is
# set here, per sheet, directly on that sheet's frame.
#
# The rule (analyst, REC-09): from the BILLING rows only, mark the first row per
# entityid Y and every other site row (later billing rows AND all shipping rows)
# N. "First" is deterministic — MIN(internalid) per entityid — so a regenerate
# marks the same row every time (REC-09's LIMIT-1-else-MIN(internalid) fallback).
# Contact sheets are excluded (REC-46 / REC-52 keep their Primary blank).
#
#   {sheet substring: (flag field name, use-type field-name candidates)}
# A sheet with a use-type column restricts "first" to its BILL_TO rows; a sheet
# without one (PARTYSITES identifying address, REC-23) considers every site row.
_FIRST_FLAG_SHEETS = {
    "partysiteuses": ("Primary Indicator", ("Part Site Use Type", "Site Use Type")),
    "acctsiteuses": ("Primary Indicator", ("Purpose",)),
    "partysites": ("Identifying Address", ()),
}


def first_flag_field(sheet_name: Optional[str]) -> Optional[str]:
    """The Primary/Identifying flag field-name the merge sets on ``sheet_name`` (REC-09
    / REC-23), or ``None`` for a sheet the merge does not flag.

    Output generation calls this to PROTECT the reshape-owned value from the per-sheet
    keep-blank / suppression: the merge decides this flag from the customer key on the
    sheet's own rows, so it must not depend on whether a project's mapping for the field
    happens to sit at ``not_applicable`` (which a gold-purge revert can leave behind).
    Returning None for the contact sheets is deliberate — their Primary stays blank
    (REC-46 / REC-52)."""
    n = _norm(sheet_name)
    for key, (flag_name, _uses) in _FIRST_FLAG_SHEETS.items():
        if key in n:
            return flag_name
    return None


# Fields the CONTACTPTS fan-out populates per contact-point row (REC-48/53/54/56/57).
# Protected the same way the first-flag fields are: the merge decides them from the
# contact's own phone/email, so a stray default (Contact Point Type = EMAIL on every
# row) or a suppression must not override the fanned value.
_CONTACTPTS_OWNED = (
    "Contact Point Type", "Email Address", "Phone Number", "Phone Line Type",
    "Contact Point Original System Reference",
)

# Party identity the merge stamps deterministically on HZ_IMP_PARTIES_T, by grain:
# the customer master rows are ORGANIZATION and take ``companyname``; the contact
# rows are PERSON and take their own first/middle/last name (REC-02/05/07/12/15/17).
# Owned so the value never depends on a per-conversion Party Type rule / name mapping
# that may sit at not_applicable or come back only "suggested" in a fresh project.
_PARTIES_OWNED = (
    "Party Type", "Organization Name",
    "Person First Name", "Person Middle Name", "Person Last Name",
)


# ── Sheet-scoped constants / blanks / reference-copies the merge stamps ──────────
# Open tracker items that are pure per-sheet decisions (a constant, a forced blank,
# or "same value as a sibling reference"). Expressed here in the engine — and owned —
# so they hold for every project instead of riding on a per-conversion mapping that a
# fresh project won't reproduce. Keyed by normalised-sheet substring.
_SHEET_CONST = {
    # RELSHIPS Subject/Object Original System = NETSUITE (REC-67/69)
    "relship": {"Subject Relationship Party Original System": "NETSUITE",
                "Object Relationship Party Original System": "NETSUITE"},
    # ROLERESP Role Responsibility Original System = NETSUITE (REC-75)
    "roleresp": {"Account Contact Role Responsibility Original System": "NETSUITE"},
}
# Forced-blank fields (REC-80/81/82/83/84/88 on RA_CUSTOMER_PROFILES; REC-25 on PARTYSITES).
_SHEET_BLANK = {
    "profile": ("Party Original System", "Party Original System Reference",
                "Account Site Source System", "Account Site Source System Reference",
                "Credit Rating", "Party Number"),
    "partysites": ("Relationship Source System Reference",),
}
def _sheet_rule_fields(n: str) -> set:
    """All field names any sheet-scoped rule touches on this (normalised) sheet."""
    out: set = set()
    for key, d in _SHEET_CONST.items():
        if key in n:
            out.update(d.keys())
    for key, flds in _SHEET_BLANK.items():
        if key in n and not (key == "partysites" and "partysiteuses" in n):
            out.update(flds)
    return out


def stamp_sheet_rules(sub: "pd.DataFrame", sheet_name: Optional[str]) -> "pd.DataFrame":
    """Apply the sheet-scoped constants / forced blanks (owned)."""
    if sub is None or len(sub) == 0:
        return sub
    n = _norm(sheet_name)
    for key, d in _SHEET_CONST.items():
        if key in n:
            for fld, val in d.items():
                _set_owned_col(sub, fld, pd.Series([val] * len(sub), index=sub.index))
    for key, flds in _SHEET_BLANK.items():
        if key in n and not (key == "partysites" and "partysiteuses" in n):
            for fld in flds:
                col = _find_col_ci(sub.columns, fld)
                if col is not None:
                    sub[col] = ""
    return sub


def merge_owned_fields(sheet_name: Optional[str]) -> set:
    """Field names the merge authoritatively populates on ``sheet_name`` — the ones
    output generation must protect from the per-sheet keep-blank / suppression / control
    default so the merge's value survives regardless of a project's mapping state.

    First-flag sheets contribute their Primary/Identifying field; the contact-points
    sheet contributes the fan-out fields it fills per contact point; the parties
    backbone contributes the party-identity fields; the accounts sheet contributes
    Account Description (REC-30)."""
    owned: set = set()
    flag = first_flag_field(sheet_name)
    if flag:
        owned.add(flag)
    n = _norm(sheet_name)
    if "contactpt" in n:
        owned.update(_CONTACTPTS_OWNED)
    if "parties" in n:
        owned.update(_PARTIES_OWNED)
    # "account" (a-c-c-o-u-n-t) only appears in HZ_IMP_ACCOUNTS_T; ACCTSITES /
    # ACCTCONTACTS normalise to "acct…", so this does not leak to the child sheets.
    if "account" in n and "site" not in n and "contact" not in n:
        owned.add("Account Description")
    owned.update(_sheet_rule_fields(n))    # constants / blanks / ref-copies
    return owned


def _fanout_contact_points(sub: "pd.DataFrame") -> "pd.DataFrame":
    """One row per contact POINT, not per contact (REC-48/53/54/56/57).

    A NetSuite contact carries an e-mail and a phone in one row, but Oracle's
    HZ_IMP_CONTACTPTS_T is one row per contact point. So each contact fans out into an
    EMAIL row (Contact Point Type=EMAIL, Email Address set) and a PHONE row (Type=PHONE,
    Phone Number set, Phone Line Type=MOBILE) — only for the points the source actually
    has. The point's Original System Reference gets the matching _EMAIL / _PHONE tag.
    A contact with neither e-mail nor phone produces NO contact-point row — an Oracle
    contact point must have a type, so a typeless row is invalid; the contact itself
    still exists on HZ_IMP_CONTACTS_T and is not lost (REC-48).
    Reads the raw e-mail/phone values threaded through as ``__email`` / ``__phone`` …"""
    if sub is None or len(sub) == 0:
        return sub
    cpt = _find_col_ci(sub.columns, "Contact Point Type")
    email_f = _find_col_ci(sub.columns, "Email Address")
    phone_f = _find_col_ci(sub.columns, "Phone Number")
    plt_f = _find_col_ci(sub.columns, "Phone Line Type")
    osr_f = _find_col_ci(sub.columns, "Contact Point Original System Reference")
    if cpt is None and email_f is None and phone_f is None:
        return sub                              # not the contact-points shape — leave it
    e_col = "__email" if "__email" in sub.columns else None
    ae_col = "__altemail" if "__altemail" in sub.columns else None
    p_col = "__phone" if "__phone" in sub.columns else None
    m_col = "__mobilephone" if "__mobilephone" in sub.columns else None
    have_ent = ENTITYID_COL in sub.columns
    have_iid = INTERNALID_COL in sub.columns

    def _v(row, col):
        if not col:
            return ""
        s = str(row.get(col, "")).strip()
        return "" if s.lower() in ("nan", "none", "null") else s

    rows: list = []
    for _, r in sub.iterrows():
        email = _v(r, e_col) or _v(r, ae_col)
        phone = _v(r, p_col) or _v(r, m_col)
        base = ""
        if have_ent or have_iid:
            base = f"{_v(r, ENTITYID_COL)}_{_v(r, INTERNALID_COL)}"
        made = False
        if email:
            row = r.copy()
            if cpt is not None:
                row[cpt] = "EMAIL"
            if email_f is not None:
                row[email_f] = email
            if phone_f is not None:
                row[phone_f] = ""
            if plt_f is not None:
                row[plt_f] = ""
            if osr_f is not None:
                row[osr_f] = f"{base}_EMAIL" if base.strip("_") else ""
            rows.append(row)
            made = True
        if phone:
            row = r.copy()
            if cpt is not None:
                row[cpt] = "PHONE"
            if phone_f is not None:
                row[phone_f] = phone
            if email_f is not None:
                row[email_f] = ""
            if plt_f is not None:
                row[plt_f] = "MOBILE"
            if osr_f is not None:
                row[osr_f] = f"{base}_PHONE" if base.strip("_") else ""
            rows.append(row)
            made = True
        if not made:
            # A contact with neither e-mail nor phone has NO contact point. Emitting a
            # typeless row (Contact Point Type blank; Original System Reference falling
            # back to the source system "NETSUITE") is an invalid HZ_IMP_CONTACTPTS_T
            # row Oracle rejects — REC-48: 199 such rows shipped. The contact still
            # exists on HZ_IMP_CONTACTS_T, so drop only the empty contact-point row.
            continue
    if not rows:
        # every contact in this chunk was point-less — return an empty frame that keeps
        # the columns, so downstream concatenation/alignment is unaffected.
        return sub.iloc[0:0]
    return pd.DataFrame(rows).reset_index(drop=True)


def _mark_first_per_entityid(sub: "pd.DataFrame", sheet_name: Optional[str]) -> "pd.DataFrame":
    """Set the sheet's primary/identifying flag ``Y`` on the first (MIN-internalid)
    billing row per entityid and ``N`` on every other row (REC-09 / REC-23).

    Pure and defensive: a sheet not in ``_FIRST_FLAG_SHEETS``, or one missing the
    flag column / the entityid / the internalid, is returned untouched — a blank
    flag beats a wrong one, and no other sheet's data is at risk."""
    n = _norm(sheet_name)
    spec = next((v for k, v in _FIRST_FLAG_SHEETS.items() if k in n), None)
    if spec is None or sub is None or len(sub) == 0:
        return sub
    flag_name, use_candidates = spec
    flag_col = _find_col_ci(sub.columns, flag_name)
    if (flag_col is None or ENTITYID_COL not in sub.columns
            or INTERNALID_COL not in sub.columns):
        return sub

    ent = sub[ENTITYID_COL].astype(str).str.strip()
    iid = sub[INTERNALID_COL].astype(str).str.strip()
    # Eligible rows: BILL_TO where the sheet distinguishes uses, else every site.
    eligible = pd.Series(True, index=sub.index)
    for cand in use_candidates:
        uc = _find_col_ci(sub.columns, cand)
        if uc is not None:
            eligible = sub[uc].astype(str).str.strip().str.upper().eq("BILL_TO")
            break

    work = pd.DataFrame(
        {"ent": ent, "iid_num": pd.to_numeric(iid, errors="coerce"), "iid_str": iid},
        index=sub.index,
    )
    work = work[eligible.values & work["ent"].ne("")]
    result = pd.Series("N", index=sub.index)
    if len(work):
        # Ascending MIN(internalid) per entityid; NaN internalids sort last so a
        # real number always wins, ties broken by the string form then by
        # first-seen (mergesort is stable).
        winners = (work.sort_values(["ent", "iid_num", "iid_str"], kind="mergesort")
                   .groupby("ent", sort=False).head(1).index)
        result.loc[winners] = "Y"
    sub = sub.copy()
    sub[flag_col] = result.values
    return sub


def assign_party_numbers(sub: "pd.DataFrame") -> None:
    """Number the parties sheet in place: NXT000001, NXT000002 … per customer
    (entityid), the org taking the bare number and each of its contact people taking
    ``_C1``, ``_C2`` … in row order (REC-06). Runs AFTER the fan-out so an org and its
    contacts — which arrive from different source files — share ONE base number, which
    a per-source SEQUENCE rule cannot do. A row whose Party Type is not PERSON is the
    organization; blank-keyed rows are left with whatever number they had."""
    pn_col = _find_col_ci(sub.columns, "Party Number")
    pt_col = _find_col_ci(sub.columns, "Party Type")
    if pn_col is None or ENTITYID_COL not in sub.columns:
        return
    eids = sub[ENTITYID_COL].astype(str).str.strip().tolist()
    ptypes = (sub[pt_col].astype(str).str.strip().str.upper().tolist()
              if pt_col is not None else [""] * len(sub))
    ordinal: dict[str, int] = {}
    person_seq: dict[str, int] = {}
    out: list[str] = []
    for e, pt in zip(eids, ptypes):
        if not e:
            out.append("")                       # nothing to key on; leave blank
            continue
        if e not in ordinal:
            ordinal[e] = len(ordinal) + 1
        base = f"NXT{ordinal[e]:06d}"
        if pt == "PERSON":
            person_seq[e] = person_seq.get(e, 0) + 1
            out.append(f"{base}_C{person_seq[e]}")
        else:
            out.append(base)
    # Keep an existing value only where we produced none (blank key).
    old = sub[pn_col].astype(str).tolist()
    sub[pn_col] = [n if n else old[i] for i, n in enumerate(out)]


def _carried(sub: "pd.DataFrame", name: str) -> "Optional[pd.Series]":
    """The threaded-through source column ``__name`` as a stripped string Series, or
    None when the merge did not carry it (see output_service carry_source_cols)."""
    col = "__" + name
    if col in sub.columns:
        return sub[col].astype(str).str.strip()
    return None


def _set_owned_col(sub: "pd.DataFrame", field_name: str, series: "pd.Series") -> None:
    """Write a target field on the reshaped sheet, matching an existing column name
    case/punct-insensitively or creating it under the plain field name (which the
    per-sheet reindex maps to the template header)."""
    col = _find_col_ci(sub.columns, field_name) or field_name
    sub[col] = list(series.values)


def set_party_identity(sub: "pd.DataFrame") -> "pd.DataFrame":
    """Stamp party identity on the PARTIES backbone deterministically, by grain.

    The customer master rows are ORGANIZATION and carry ``companyname`` as the
    Organization Name; the contact rows are PERSON and carry their own first/middle/
    last name. Done in the engine (and owned via ``merge_owned_fields``) so it holds
    for every current and future project without depending on a Party Type derivation
    rule or firstname/lastname mapping being applied per conversion — the exact fan-out
    gap the regression surfaced (REC-02/05/07/10/12/15/17)."""
    if GRAIN_COL not in sub.columns or len(sub) == 0:
        return sub
    g = sub[GRAIN_COL].astype(str).str.strip().str.lower()
    is_contact = g.eq(CONTACT)
    is_org = ~is_contact

    pt = pd.Series(["ORGANIZATION"] * len(sub), index=sub.index)
    pt[is_contact] = "PERSON"
    _set_owned_col(sub, "Party Type", pt)

    cn = _carried(sub, "companyname")
    if cn is not None:
        on = pd.Series([""] * len(sub), index=sub.index)
        on[is_org] = cn[is_org]                 # org name only on the org party
        _set_owned_col(sub, "Organization Name", on)

    for fld, src in (("Person First Name", "firstname"),
                     ("Person Middle Name", "middlename"),
                     ("Person Last Name", "lastname")):
        sc = _carried(sub, src)
        if sc is None:
            continue
        pv = pd.Series([""] * len(sub), index=sub.index)
        pv[is_contact] = sc[is_contact]         # person name only on the contact party
        _set_owned_col(sub, fld, pv)
    return sub


def _stamp_account_description(sub: "pd.DataFrame", sheet_name: Optional[str]) -> "pd.DataFrame":
    """Account Description <- companyname on HZ_IMP_ACCOUNTS_T (REC-30). Owned, so it
    survives regardless of the per-conversion mapping state."""
    n = _norm(sheet_name)
    if "account" in n and "site" not in n and "contact" not in n:
        cn = _carried(sub, "companyname")
        if cn is not None:
            _set_owned_col(sub, "Account Description", cn)
    return sub


def sheet_rows(df: pd.DataFrame, sheet_name: Optional[str]) -> pd.DataFrame:
    """The subset of the merged frame that belongs on ``sheet_name``.

    Party/account sheets get the party-grain rows, de-duplicated to one per
    ``entityid``; site and contact sheets get their own grain's rows unchanged. Every
    returned row's party link is retargeted to its customer key. A sheet whose grain
    is unknown, or whose grain has no rows in this load, is given the whole frame back
    (the previous behaviour) rather than an empty sheet — an empty backbone sheet
    would break the load, and a slightly over-full one will not.

    HZ_IMP_PARTIES_T is special: it holds EVERY party. It gets the customer orgs (the
    party grain, de-duplicated) AND the contact people (the contact grain), so a
    contact becomes a PERSON party with its own name, type and number (REC-05/06/07/13),
    while every other party-grain sheet stays one row per customer.
    """
    if df is None or GRAIN_COL not in df.columns or len(df) == 0:
        return df
    grain = sheet_grain(sheet_name)
    if grain is None:
        return df
    # Case/space-insensitive so a client cleansing profile that touches the tag can
    # never silently turn the reshape off (grain constants are lower-case).
    g = df[GRAIN_COL].astype(str).str.strip().str.lower()

    # ── The parties backbone: orgs (deduped party grain) + contact PERSON parties ──
    if grain == PARTY and "parties" in _norm(sheet_name):
        party_sub = df[g == PARTY]
        contact_sub = df[g == CONTACT]
        if len(party_sub) == 0 and len(contact_sub) == 0:
            return df
        parts = []
        if len(party_sub):
            parts.append(_dedupe_party_grain(party_sub))
        if len(contact_sub):
            parts.append(contact_sub)             # one PERSON party per contact, not deduped
        sub = (pd.concat(parts, ignore_index=True) if len(parts) > 1
               else parts[0]).reset_index(drop=True)
        set_party_identity(sub)                    # Party Type / Org Name / Person names
        _set_party_link(sub, own_internalid=True)  # each party's ref is its OWN internalid
        assign_party_numbers(sub)                  # NXT / _C{n} per customer (REC-06)
        return sub

    sub = df[g == grain]
    if len(sub) == 0:
        # No source of this grain in the load — don't empty the sheet.
        return df
    if grain == PARTY and ENTITYID_COL in sub.columns:
        sub = _dedupe_party_grain(sub)
    sub = sub.reset_index(drop=True)
    _set_party_link(sub)
    sub = _stamp_account_description(sub, sheet_name)   # Account Description <- companyname (REC-30)
    sub = stamp_sheet_rules(sub, sheet_name)            # NETSUITE constants / PROFILES blanks / ref-copies
    # Contact points: fan each contact into its e-mail and phone points (REC-48/53/54/
    # 56/57). Only the contact-points sheet; every other contact sheet stays one row
    # per contact.
    if grain == CONTACT and "contactpt" in _norm(sheet_name):
        sub = _fanout_contact_points(sub)
    # Primary / Identifying flag: Y on the first billing row per customer, N on the
    # rest (REC-09 / REC-23). No-op on any sheet without such a flag.
    sub = _mark_first_per_entityid(sub, sheet_name)
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
