"""Coded-value (LOV) intelligence for Oracle FBDI columns.

Two very different kinds of coded column live in an FBDI template, and conflating
them is how you silently load bad data into Fusion:

1. ENUMERATED — the description literally states the codes *and their meaning*:

     "Contains one of the following values: Y or N. If Y, then the item can be
      added to an outside processing purchase order. If N, then it can't."
     "Contains one of the following values: 1 or 2. If 1, then it indicates Yes.
      If 2, it indicates No."
     "Valid values are Default, Fixed, and No Default."

   These are GROUNDED IN THE TEMPLATE. We parse the codes, the per-code meaning,
   and — critically — the *polarity* (Oracle spells out which code is the negative
   one via "don't / isn't / can't / indicates No"). Polarity is what lets a
   descriptive source value ("Stocked in warehouse", "Not tracked") resolve to the
   right code deterministically, with no AI and no guessing.

2. NAMED LOOKUP — the description names an Oracle lookup type instead:

     "A list of accepted values is defined in the lookup type EGP_PLANNING_MAKE_BUY.
      Review and update the value for this attribute using the Manage Standard
      Lookups task."

   The codes are NOT in the template. They live in the customer's Fusion instance
   and are configurable there. So we never invent them. We capture the lookup type,
   resolve it against a small table of Oracle-standard codes where a well-known one
   exists (flagged ``verified=False``), and otherwise leave the column UNRESOLVED
   and flag it — a flagged column a human fixes beats a guessed code that loads
   clean and is wrong.

The instance's real codes can be supplied via ``upsert_lookup`` (Setup and
Maintenance → Manage Standard Lookups → export), which marks them verified and
makes them authoritative over anything seeded here.
"""
from __future__ import annotations

import difflib
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Description parsing
# ---------------------------------------------------------------------------

_LOOKUP_RE = re.compile(
    r"lookup\s+type\s+([A-Z][A-Z0-9_]{3,})",
    re.IGNORECASE,
)

# "Contains one of the following values: Y or N."  /  "...values:1 or 2."
_ENUM_FOLLOWING_RE = re.compile(
    r"following\s+values?\s*:?\s*([^.]{1,80}?)\s*(?:\.|$)",
    re.IGNORECASE,
)
# "Valid values are Default, Fixed, and No Default."
_ENUM_VALID_RE = re.compile(
    r"(?:valid|accepted|possible)\s+values?\s+(?:are|include)\s*:?\s*([^.]{1,120}?)\s*(?:\.|$)",
    re.IGNORECASE,
)
# "Values include Prepositioned and Synchronized."
_ENUM_INCLUDE_RE = re.compile(
    r"\bvalues\s+include\s*:?\s*([^.]{1,120}?)\s*(?:\.|$)",
    re.IGNORECASE,
)

_SPLIT_RE = re.compile(r"\s*(?:,|\bor\b|\band\b|/)\s*", re.IGNORECASE)

# "If Y, then the item can be added..."  /  "If 2, it indicates No."
_CLAUSE_RE = re.compile(
    r"\bIf\s+([A-Za-z0-9][A-Za-z0-9 ]{0,14}?)\s*,\s*(?:then\s+)?(.*?)"
    r"(?=\bIf\s+[A-Za-z0-9][A-Za-z0-9 ]{0,14}?\s*,|$)",
    re.IGNORECASE | re.DOTALL,
)

_DEFAULT_RE = re.compile(
    r"default\s+value\s+is\s+([A-Za-z0-9][A-Za-z0-9 _-]{0,24}?)\s*(?:\.|$)",
    re.IGNORECASE,
)

# Oracle's way of saying "this code is the negative one".
_NEG_RE = re.compile(
    r"\b(?:don'?t|doesn'?t|do not|does not|isn'?t|is not|aren'?t|are not|"
    r"can'?t|cannot|won'?t|will not|never|no|not)\b",
    re.IGNORECASE,
)
_INDICATES_NO_RE = re.compile(r"indicates?\s+(?:a\s+)?no\b", re.IGNORECASE)
_INDICATES_YES_RE = re.compile(r"indicates?\s+(?:a\s+)?yes\b", re.IGNORECASE)


def parse_lookup_type(description: Optional[str]) -> Optional[str]:
    """``EGP_PLANNING_MAKE_BUY`` out of "...defined in the lookup type EGP_PLANNING_MAKE_BUY."."""
    if not description:
        return None
    m = _LOOKUP_RE.search(description)
    if not m:
        return None
    lt = m.group(1).strip().rstrip(".").upper()
    # Guard against grabbing an English word out of a malformed sentence.
    if "_" not in lt or len(lt) < 5:
        return None
    return lt


def parse_default_if_blank(description: Optional[str]) -> Optional[str]:
    if not description:
        return None
    m = _DEFAULT_RE.search(description)
    if not m:
        return None
    val = m.group(1).strip()
    # "The default value is true." on a 1/2 column is prose, not a loadable code —
    # only keep defaults that are short, code-shaped tokens.
    if len(val) > 24:
        return None
    return val


def _candidate_codes(description: str) -> list[str]:
    for rx in (_ENUM_FOLLOWING_RE, _ENUM_VALID_RE, _ENUM_INCLUDE_RE):
        m = rx.search(description)
        if not m:
            continue
        blob = m.group(1).strip()
        parts = [p.strip().strip("'\"") for p in _SPLIT_RE.split(blob)]
        codes = [p for p in parts if p and len(p) <= 30]
        # De-dupe, preserve order.
        seen: set[str] = set()
        out: list[str] = []
        for c in codes:
            k = c.upper()
            if k not in seen:
                seen.add(k)
                out.append(c)
        if 2 <= len(out) <= 12:
            return out
    return []


def _clause_by_code(description: str, codes: list[str]) -> dict[str, str]:
    """Map each code to the sentence Oracle uses to explain it."""
    upper = {c.upper(): c for c in codes}
    out: dict[str, str] = {}
    for m in _CLAUSE_RE.finditer(description):
        token = m.group(1).strip().upper()
        if token in upper and upper[token] not in out:
            out[upper[token]] = " ".join(m.group(2).split())
    return out


def _polarity(codes: list[str], clauses: dict[str, str]) -> dict[str, Optional[bool]]:
    """True == the affirmative code, False == the negative code, None == not a boolean.

    Y/N is unambiguous. For 1/2 columns Oracle always writes one clause in the
    negative ("If 2, then don't restrict...", "If 2, it indicates No"), so exactly
    one negated clause out of two is a reliable signal.
    """
    up = [c.upper() for c in codes]
    if set(up) == {"Y", "N"}:
        return {c: (c.upper() == "Y") for c in codes}

    if len(codes) != 2:
        return {c: None for c in codes}

    verdict: dict[str, Optional[bool]] = {}
    for c in codes:
        cl = clauses.get(c)
        if not cl:
            verdict[c] = None
            continue
        if _INDICATES_NO_RE.search(cl):
            verdict[c] = False
        elif _INDICATES_YES_RE.search(cl):
            verdict[c] = True
        elif _NEG_RE.search(cl):
            verdict[c] = False
        else:
            verdict[c] = True

    trues = [c for c, v in verdict.items() if v is True]
    falses = [c for c, v in verdict.items() if v is False]
    # Only trust it when the two clauses disagree — exactly one affirmative and one
    # negative. Anything else (both positive, both negative, one missing) is a
    # semantic enum like Consigned, not a boolean, and we must not force polarity.
    if len(trues) == 1 and len(falses) == 1:
        return verdict
    return {c: None for c in codes}


def parse_allowed_values(description: Optional[str]) -> list[dict[str, Any]]:
    """Enumerated codes grounded in the template's own description text."""
    if not description:
        return []
    codes = _candidate_codes(description)
    if not codes:
        return []
    clauses = _clause_by_code(description, codes)
    pol = _polarity(codes, clauses)
    return [
        {
            "code": c,
            "meaning": clauses.get(c) or "",
            "polarity": pol.get(c),
            "source": "template",
        }
        for c in codes
    ]


# ---------------------------------------------------------------------------
# Oracle-standard lookup codes (NOT verified against the customer's instance)
# ---------------------------------------------------------------------------
# Deliberately small. Every entry here is a lookup whose codes are Oracle-seeded
# and stable across instances, but they are still marked verified=False: a customer
# can edit them in Manage Standard Lookups. Anything resolved from this table is
# surfaced for human confirmation rather than trusted silently. We do NOT populate
# lookups we cannot ground — an empty list means "flag it", which is the safe
# failure mode.

ORACLE_STANDARD_LOOKUPS: dict[str, list[dict[str, Any]]] = {
    "EGP_PLANNING_MAKE_BUY": [
        {"code": "1", "meaning": "Make"},
        {"code": "2", "meaning": "Buy"},
    ],
    "EGP_LOT_CONTROL_CODE_TYPE": [
        {"code": "1", "meaning": "No lot control"},
        {"code": "2", "meaning": "Full lot control"},
    ],
    "EGP_SHELF_LIFE_CODE_TYPE": [
        {"code": "1", "meaning": "No shelf life control"},
        {"code": "2", "meaning": "Shelf life days"},
        {"code": "4", "meaning": "User-defined expiration date"},
    ],
    "EGP_SERIAL_NUMBER_CONTROL_TYPE": [
        {"code": "1", "meaning": "No serial number control"},
        {"code": "2", "meaning": "Predefined serial numbers"},
        {"code": "5", "meaning": "At organization receipt"},
        {"code": "6", "meaning": "At sales order issue"},
    ],
    "EGP_BOM_EFFEC_CTRL": [
        {"code": "1", "meaning": "Date"},
        {"code": "2", "meaning": "Model or unit number"},
    ],
}


def standard_lookup_values(lookup_type: Optional[str]) -> list[dict[str, Any]]:
    if not lookup_type:
        return []
    rows = ORACLE_STANDARD_LOOKUPS.get(lookup_type.strip().upper())
    if not rows:
        return []
    return [
        {
            "code": r["code"],
            "meaning": r["meaning"],
            "polarity": None,
            "source": "oracle_standard",
            "verified": False,
        }
        for r in rows
    ]


def enrich_field(field_name: str, description: Optional[str]) -> dict[str, Any]:
    """Everything the parser should attach to an FBDIField for a coded column."""
    lookup_type = parse_lookup_type(description)
    allowed = parse_allowed_values(description)
    notes: list[str] = []

    if not allowed and lookup_type:
        allowed = standard_lookup_values(lookup_type)
        if allowed:
            notes.append(
                f"Codes for lookup type {lookup_type} are Oracle-standard defaults and "
                f"are NOT verified against your Fusion instance. Confirm them in Setup "
                f"and Maintenance → Manage Standard Lookups."
            )
        else:
            notes.append(
                f"Coded column. Accepted values come from lookup type {lookup_type} in "
                f"your Fusion instance (Manage Standard Lookups) and are not published "
                f"in the template. Import the lookup codes so values can be mapped "
                f"instead of guessed."
            )

    return {
        "lookup_type": lookup_type,
        "allowed_values": allowed,
        "default_if_blank": parse_default_if_blank(description),
        "validation_notes": " ".join(notes) or None,
    }


# ---------------------------------------------------------------------------
# Value resolution — source value  ->  Oracle code
# ---------------------------------------------------------------------------

_TRUE_WORDS = {
    "y", "yes", "true", "t", "1", "x", "on", "active", "enabled", "enable",
    "checked", "applicable", "allow", "allowed", "required", "include",
    "included", "stocked", "stock", "in stock", "tracked", "available",
    "valid", "positive", "controlled", "restricted", "mandatory",
}
_FALSE_WORDS = {
    "n", "no", "false", "f", "0", "off", "inactive", "disabled", "disable",
    "unchecked", "not applicable", "na", "n/a", "none", "deny", "denied",
    "excluded", "exclude", "not stocked", "nonstock", "non-stock",
    "not tracked", "untracked", "unavailable", "invalid", "negative",
    "uncontrolled", "unrestricted", "optional",
}
# NB: "make"/"buy" are deliberately NOT boolean words. Make-or-Buy is a two-value
# lookup, not a yes/no — treating "buy" as false would quietly write the wrong code.

# Synonyms for Oracle's own meaning text, so a source system that says
# "Manufactured in house" still lands on the Make code without an AI call.
_MEANING_SYNONYMS: dict[str, set[str]] = {
    "make": {
        "manufacture", "manufactured", "manufacturing", "made", "made in house",
        "in house", "produce", "produced", "production", "build", "built",
        "assemble", "assembled", "internal", "fabricated", "self manufactured",
    },
    "buy": {
        "purchase", "purchased", "purchasing", "procure", "procured",
        "procurement", "outsource", "outsourced", "external", "vendor",
        "supplier", "bought", "resale", "resell", "third party",
    },
    "no lot control": {"no control", "not lot controlled", "no lot", "not controlled", "none"},
    "full lot control": {"lot controlled", "full control", "lot control"},
    "no serial number control": {"no control", "not serialized", "no serial", "none"},
    "predefined serial numbers": {"predefined", "serialized", "serial numbers"},
    "at organization receipt": {"at receipt", "on receipt", "receipt"},
    "at sales order issue": {"at issue", "on issue", "sales order issue"},
    "no shelf life control": {"no control", "no shelf life", "none"},
    "shelf life days": {"shelf life", "item shelf life", "days"},
    "user-defined expiration date": {"user defined", "expiration date", "user-defined expiry"},
    "date": {"date based", "by date"},
    "model or unit number": {"model", "unit number", "unit"},
}


def _norm(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = re.sub(r"[\s_\-]+", " ", s)
    return s.strip()


def _boolean_hint(raw: str) -> Optional[bool]:
    """Read a descriptive source value as a yes/no, if it plainly is one.

    Handles the two cases the user hit head-on: a Stocked column that says
    "Stocked in warehouse" / "Not stocked", and free text that leads with a
    negation ("Do not track", "No serial control").
    """
    n = _norm(raw)
    if not n:
        return None
    if n in _TRUE_WORDS:
        return True
    if n in _FALSE_WORDS:
        return False
    # Leading negation in a descriptive phrase.
    if re.match(r"^(not|no|non|never|dont|don't|do not|excluded?|disabled?)\b", n):
        return False
    # A phrase built around an affirmative token ("stocked in warehouse").
    tokens = set(n.split())
    if tokens & _FALSE_WORDS:
        return False
    if tokens & _TRUE_WORDS:
        return True
    return None


def resolve_value(raw: Any, allowed: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve one source value against a coded column's allowed values.

    Returns ``{code, method, confidence}``; ``code`` is None when we could not
    ground the value — the caller must flag it, never invent a code.
    """
    miss = {"code": None, "method": "unresolved", "confidence": 0.0}
    if not allowed:
        return miss
    n = _norm(raw)
    if not n:
        return miss

    by_code = {_norm(a.get("code")): a for a in allowed if a.get("code") is not None}

    # 1. Already a valid code — pass straight through.
    if n in by_code:
        return {"code": str(by_code[n]["code"]), "method": "already a valid code", "confidence": 1.0}

    # 2. Exact match on Oracle's own meaning ("Make" -> 1).
    for a in allowed:
        meaning = _norm(a.get("meaning"))
        if meaning and meaning == n:
            return {"code": str(a["code"]), "method": "matched Oracle meaning", "confidence": 0.97}

    # 3. Boolean polarity — the template told us which code means no.
    pol = {a["code"]: a.get("polarity") for a in allowed if a.get("polarity") is not None}
    if pol:
        hint = _boolean_hint(raw)
        if hint is not None:
            for code, is_true in pol.items():
                if is_true is hint:
                    return {
                        "code": str(code),
                        "method": f"read as {'yes' if hint else 'no'} → template polarity",
                        "confidence": 0.92,
                    }

    # 4. Synonym of Oracle's meaning ("Manufactured in house" -> Make).
    for a in allowed:
        syns = _MEANING_SYNONYMS.get(_norm(a.get("meaning")))
        if not syns:
            continue
        for s in syns:
            if n == s or re.search(rf"\b{re.escape(s)}\b", n):
                return {
                    "code": str(a["code"]),
                    "method": f"synonym of Oracle meaning '{a.get('meaning')}'",
                    "confidence": 0.9,
                }

    # 5. Fuzzy match against the meanings.
    best_code, best_score = None, 0.0
    for a in allowed:
        meaning = _norm(a.get("meaning"))
        if not meaning:
            continue
        score = difflib.SequenceMatcher(None, n, meaning).ratio()
        # Whole-word containment is a stronger signal than raw character overlap.
        if re.search(rf"\b{re.escape(n)}\b", meaning) or re.search(rf"\b{re.escape(meaning)}\b", n):
            score = max(score, 0.85)
        if score > best_score:
            best_code, best_score = a["code"], score
    if best_code is not None and best_score >= 0.72:
        return {
            "code": str(best_code),
            "method": f"text similarity to Oracle meaning ({best_score:.0%})",
            "confidence": round(min(best_score, 0.88), 2),
        }

    return miss


def build_crosswalk(values: list[Any], allowed: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Distinct source value -> resolution, for every value in a coded column."""
    out: dict[str, dict[str, Any]] = {}
    for v in values:
        key = "" if v is None else str(v)
        if not key.strip() or key in out:
            continue
        out[key] = resolve_value(v, allowed)
    return out


def is_coded(field: Any) -> bool:
    return bool(getattr(field, "allowed_values", None)) or bool(getattr(field, "lookup_type", None))


# ---------------------------------------------------------------------------
# Enforcement — applied to the generated frame just before it becomes FBDI
# ---------------------------------------------------------------------------

def enforce_coded_values(df: Any, fields: list) -> dict[str, dict[str, Any]]:
    """Make every coded (LOV) column hold a value Oracle will actually accept.

    Fusion rejects a coded column containing anything outside its accepted list —
    "Make" in a NUMBER column whose codes are 1/2, "Stocked in warehouse" where it
    wants Y/N — and the failure surfaces as an opaque load error much later. This
    resolves each distinct value against the codes mined from the template and
    rewrites the column in place.

    Policy for a value we CANNOT ground:
      * optional column → blank it. An empty optional column loads; an invalid one
        fails the file. The dropped value is reported, never silently lost.
      * required column → leave it untouched and report an error. We will not
        invent a code for a mandatory field, and blanking it would just swap one
        load failure for another while hiding the cause.
      * column whose codes live in the customer's instance (lookup_type with no
        published values) → change nothing, flag for confirmation. We don't know
        what's valid there, so we don't get to have an opinion.
    """
    report: dict[str, dict[str, Any]] = {}
    for f in fields:
        col = getattr(f, "field_name", None)
        if not col or col not in df.columns:
            continue
        allowed = list(getattr(f, "allowed_values", None) or [])
        lookup_type = getattr(f, "lookup_type", None)
        if not allowed and not lookup_type:
            continue

        required = bool(getattr(f, "required", False))
        distinct = [v for v in df[col].dropna().unique().tolist() if str(v).strip() != ""]

        if not allowed:
            report[col] = {
                "status": "unverified",
                "required": required,
                "lookup_type": lookup_type,
                "distinct_values": [str(v) for v in distinct[:25]],
                "message": (
                    f"Accepted codes for {lookup_type} come from your Fusion instance "
                    f"(Manage Standard Lookups) and aren't published in the template. "
                    f"Values were passed through unchanged — import the lookup codes to "
                    f"have them validated and mapped."
                ),
            }
            continue

        crosswalk = build_crosswalk(distinct, allowed)
        resolved = {k: r["code"] for k, r in crosswalk.items() if r["code"] is not None}
        unresolved = [k for k, r in crosswalk.items() if r["code"] is None]
        changed = {k: v for k, v in resolved.items() if k != v}

        blanked = 0
        if changed or unresolved:
            def _fix(v: Any) -> Any:
                nonlocal blanked
                if v is None or str(v).strip() == "":
                    return v
                code = resolved.get(str(v))
                if code is not None:
                    return code
                if required:
                    return v  # reported as an error rather than guessed at
                blanked += 1
                return ""
            df[col] = df[col].apply(_fix)

        unverified_codes = any(a.get("source") == "oracle_standard" for a in allowed)
        if unresolved and required:
            status = "error"
        elif unresolved or unverified_codes:
            status = "confirm"
        else:
            status = "ok"

        report[col] = {
            "status": status,
            "required": required,
            "lookup_type": lookup_type,
            "allowed_codes": [str(a.get("code")) for a in allowed],
            "codes_are_verified": not unverified_codes,
            "converted": [
                {"from": k, "to": v, "how": crosswalk[k]["method"]}
                for k, v in changed.items()
            ][:25],
            "unresolved_values": [str(v) for v in unresolved[:25]],
            "blanked_cells": blanked,
        }

    return report
