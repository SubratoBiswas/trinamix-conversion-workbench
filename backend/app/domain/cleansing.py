"""Value-level cleansing rules for the converted output frame.

Runs AFTER duplicate decisions (see ``output_service.build_converted_dataframe``),
so what gets cleansed is the row that actually ships — the survivor of a merge or
a keep-survivor verdict, never a row the analyst already dropped. That ordering is
deliberate: cleansing before detection would silently move rows between clusters
under a reviewer mid-review.

Four rule families, each independently switchable per field:

    whitespace_punct   trim, collapse internal runs, drop edge punctuation
    special_chars      control/zero-width removal + unicode normalisation
    case               smart title case for names, upper for codes
    legal_suffix       Ltd / Ltd. / Limited -> one canonical form

Pure (stdlib + pandas), so every rule is unit-testable without the Beanie/Mongo
stack — same reason ``decision_engine`` and ``merge_dedupe`` were extracted.

DEFAULTS ARE CONSERVATIVE ON PURPOSE. ``whitespace_punct`` and ``special_chars``
only remove characters that carry no meaning in an FBDI payload, so they are on by
default. ``case`` and ``legal_suffix`` REWRITE legal entity names — "Acme Limited"
becoming "Acme LTD" is a business decision, not a typo fix — so they are off until
switched on per field, after the reviewer has seen the before/after preview.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Optional

import pandas as pd

WHITESPACE_PUNCT = "whitespace_punct"
SPECIAL_CHARS = "special_chars"
CASE = "case"
LEGAL_SUFFIX = "legal_suffix"

FAMILIES = (WHITESPACE_PUNCT, SPECIAL_CHARS, CASE, LEGAL_SUFFIX)
SAFE_FAMILIES = (WHITESPACE_PUNCT, SPECIAL_CHARS)

FAMILY_LABEL = {
    WHITESPACE_PUNCT: "Whitespace & edge punctuation",
    SPECIAL_CHARS: "Special & non-printable characters",
    CASE: "Case normalisation",
    LEGAL_SUFFIX: "Legal suffix standardisation",
}

_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)

# Stripped from both ends of a text value. `-` is included because a trailing or
# leading dash is always noise, but numeric values are guarded below so a negative
# number is never turned positive.
_EDGE = ".,;:!?'\"`_-–— \t"
# Runs of these collapse to a single character ("DHABI.." -> "DHABI."). Only dots
# and commas: a run of dashes can be meaningful in a code ("US--TX" is unusual but
# not obviously wrong), while ".." never is.
_RUNS = re.compile(r"([.,])\1+")

_NUMERICISH = re.compile(r"^[\s+\-]?[\d.,]+\s*%?$")

_SMART = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...",
}
_SMART_TABLE = {ord(k): v for k, v in _SMART.items()}

# Canonical forms. Deliberately excludes 2-letter tokens that collide with real
# words in a supplier name (AS, AB, SA, OY, KK) — standardising those would rewrite
# "Nextracker AS Holdings" on a false positive. Suffixes are matched only in the
# trailing region of the name (see ``_apply_legal_suffix``).
_LEGAL_CANON = {
    "ltd": "Ltd", "ltd.": "Ltd", "limited": "Ltd",
    "inc": "Inc", "inc.": "Inc", "incorporated": "Inc",
    "corp": "Corp", "corp.": "Corp", "corporation": "Corp",
    "co": "Co", "co.": "Co", "company": "Co",
    "llc": "LLC", "l.l.c.": "LLC", "l.l.c": "LLC",
    "plc": "PLC", "p.l.c.": "PLC",
    "gmbh": "GmbH", "pty": "Pty", "pvt": "Pvt", "private": "Pvt",
    "sarl": "SARL", "srl": "SRL", "spa": "SpA",
    "bv": "BV", "b.v.": "BV", "nv": "NV", "n.v.": "NV", "ag": "AG",
}
_LEGAL_WINDOW = 3          # only the last N tokens are treated as a suffix region

# Tokens kept verbatim by smart title case.
_KEEP_UPPER = {
    "LLC", "PLC", "LTD", "INC", "CORP", "USA", "UK", "UAE", "BV", "NV", "AG",
    "SA", "SL", "AB", "AS", "OY", "KK", "SRL", "SPA", "II", "III", "IV", "VI",
    "VII", "VIII", "IX", "XI", "XII", "PO", "HQ", "IT", "HR", "RD", "ST",
}
_LOWER_PARTICLES = {
    # English
    "of", "and", "the", "for",
    # Iberian / Italian / French / Dutch / German. "e" and "y" are the Portuguese
    # and Spanish "and" — without them "1 PLAN CONSULTORIA E ASSESSORIA" title-cases
    # to a stranded capital E.
    "de", "da", "das", "do", "dos", "del", "della", "delle", "di", "du", "des",
    "der", "den", "la", "le", "les", "el", "van", "von", "ter", "e", "y", "et",
}

# Column-name substrings that identify a value as a code (upper-cased) rather than
# a human name (title-cased). Checked before the name heuristic.
_CODE_HINTS = ("code", "currency", "country", "state", "province", "uom",
               "unit of measure", "lookup", "type", "status", "flag", "method",
               "terms", "class", "category")
_NAME_HINTS = ("name", "description", "address", "city", "town", "contact",
               "title", "supplier", "customer", "party", "vendor", "organization")


def _s(v: Any) -> str:
    return "" if v is None else str(v)


def _norm_protect(s: str) -> str:
    """Match key for protected values — case/whitespace-insensitive, so a control
    default of "G-Treasury" also shields "g-treasury  " in the data."""
    return _WS.sub(" ", _s(s)).strip().casefold()


def is_text_column(series: pd.Series) -> bool:
    """True for columns holding strings.

    NOT ``dtype == object``: pandas 3 gives string columns a dedicated ``str``
    dtype, so the object test silently returns False and the whole cleanse pass
    no-ops. The repo pins pandas 2.2.3 where object is correct, but a pin is not a
    guarantee and a cleansing rule that quietly does nothing is exactly the class
    of failure this module exists to prevent.
    """
    dt = series.dtype
    if dt == object:
        return True
    return str(dt).lower() in {"str", "string", "string[python]", "string[pyarrow]"}


def _looks_numeric(s: str) -> bool:
    return bool(_NUMERICISH.match(s))


def _apply_special(s: str, *, ascii_fold: bool = False) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(_SMART_TABLE)
    s = s.translate(_ZERO_WIDTH)
    s = _CONTROL.sub("", s)
    if ascii_fold:
        s = "".join(c for c in unicodedata.normalize("NFKD", s)
                    if not unicodedata.combining(c))
    return s


def _apply_whitespace_punct(s: str) -> str:
    s = _WS.sub(" ", s).strip()
    if not s or _looks_numeric(s):
        return s
    s = _RUNS.sub(r"\1", s)
    s = s.strip(_EDGE)
    return _WS.sub(" ", s).strip()


def _title_token(tok: str) -> str:
    if not tok:
        return tok
    bare = tok.strip("().,")
    if bare.upper() in _KEEP_UPPER:
        return tok.replace(bare, bare.upper())
    # An existing acronym (all caps, no vowels or very short) is left alone:
    # "CRRC" must not become "Crrc".
    if bare.isupper() and (len(bare) <= 4 or not any(c in "AEIOU" for c in bare)):
        return tok
    if "'" in bare and len(bare) > 2:                      # O'Brien
        h, _, t = bare.partition("'")
        return tok.replace(bare, f"{h.capitalize()}'{t.capitalize()}")
    if bare[:2].lower() == "mc" and len(bare) > 3:         # McDonald
        return tok.replace(bare, "Mc" + bare[2:].capitalize())
    if "-" in bare:                                        # Smith-Jones
        return tok.replace(bare, "-".join(p.capitalize() for p in bare.split("-")))
    return tok.replace(bare, bare.capitalize())


def _smart_title(s: str) -> str:
    toks = s.split(" ")
    out = []
    for i, t in enumerate(toks):
        if i and t.strip("().,").lower() in _LOWER_PARTICLES:
            out.append(t.lower())
        else:
            out.append(_title_token(t))
    return " ".join(out)


def _column_kind(column: str) -> Optional[str]:
    c = (column or "").strip().lower()
    if any(h in c for h in _CODE_HINTS):
        return "code"
    if any(h in c for h in _NAME_HINTS):
        return "name"
    return None


def _apply_case(s: str, kind: Optional[str]) -> str:
    if not s or _looks_numeric(s):
        return s
    if kind == "code":
        return s.upper()
    if kind == "name":
        return _smart_title(s)
    return s


def _apply_legal_suffix(s: str) -> str:
    if not s:
        return s
    toks = s.split(" ")
    start = max(0, len(toks) - _LEGAL_WINDOW)
    changed = False
    for i in range(start, len(toks)):
        bare = toks[i].strip("(),")
        canon = _LEGAL_CANON.get(bare.lower())
        if canon and canon != bare:
            toks[i] = toks[i].replace(bare, canon)
            changed = True
    return " ".join(toks) if changed else s


def clean_value(value: Any, *, column: str = "", families: Iterable[str] = SAFE_FAMILIES,
                ascii_fold: bool = False, case_kind: Optional[str] = None,
                protected: Optional[set] = None) -> str:
    """Run the enabled families over one value, in dependency order.

    Order matters: unicode normalisation first (so smart quotes become plain ones
    before punctuation stripping looks at them), then whitespace/punctuation, then
    case, then legal suffixes LAST so their canonical capitalisation survives the
    case pass rather than being title-cased back.

    ``protected`` holds values a rule, strategy or control default deliberately
    SET — those are decisions, not dirty data, and cleansing must not rewrite
    them. Without it, legal-suffix standardisation turned the Oracle lookup code
    ``CORPORATION`` into ``Corp`` on 1,392 rows of a required field.
    """
    fam = set(families or ())
    s = _s(value)
    if not s.strip():
        return s
    if protected and _norm_protect(s) in protected:
        return s
    kind = case_kind if case_kind is not None else _column_kind(column)
    if SPECIAL_CHARS in fam:
        s = _apply_special(s, ascii_fold=ascii_fold)
    if WHITESPACE_PUNCT in fam:
        s = _apply_whitespace_punct(s)
    if CASE in fam:
        s = _apply_case(s, kind)
    # Only ever on human names. A lookup column can legitimately contain a token
    # that reads like a legal suffix — Tax Organization Type = CORPORATION is the
    # Oracle CODE, not a company called "Corporation".
    if LEGAL_SUFFIX in fam and kind == "name":
        s = _apply_legal_suffix(s)
    return s


def default_profile(columns: Iterable[str]) -> dict:
    """The profile applied when the conversion has none saved.

    CHANGED 10-Aug (Subrato): data cleansing is now OFF by default and OPT-IN per
    rule from the Output Preview. Cleansing was silently rewriting real data — a
    trailing full stop on a company name, a leading zero on an account/site number,
    a source literal "None" — and the analyst asked for raw source values to ship
    unchanged unless a cleansing rule is deliberately switched on. So the default
    profile now enables NO families; a saved profile (set from the Output Preview
    cleansing selector) turns specific ones back on."""
    return {"families": [], "ascii_fold": False,
            "per_field": {}, "exclude_fields": [], "value_overrides": {}}


def resolve_families(column: str, profile: dict) -> set[str]:
    """Families active for one column: the profile default, overridden per field."""
    if column in (profile.get("exclude_fields") or []):
        return set()
    per = (profile.get("per_field") or {}).get(column)
    if per is None:
        # An EXPLICIT empty families list means "no cleansing" and must be honoured —
        # `families or SAFE_FAMILIES` treated [] as falsy and silently re-enabled the
        # safe families, which is exactly the default-off behaviour this change is
        # meant to deliver. Only a MISSING key (None) falls back to the safe set.
        fams = profile.get("families")
        if fams is None:
            fams = SAFE_FAMILIES
        return set(fams)
    return set(per)


def overrides_for(column: str, profile: dict) -> dict:
    """Analyst corrections for one column: {normalised original -> replacement}.

    An override wins over every rule. It is how a reviewer fixes a specific bad
    result — "leave CORPORATION alone", "spell this one Ltda" — without having to
    disable a family that is right about the other 5,000 values.
    """
    raw = (profile.get("value_overrides") or {}).get(column) or {}
    return {_norm_protect(k): v for k, v in raw.items()}


def cleanse_frame(df: pd.DataFrame, profile: Optional[dict] = None,
                  protected: Optional[set] = None) -> tuple[pd.DataFrame, list]:
    """Apply the profile to every text column. Returns (frame, findings).

    findings = [{field, rule, count, examples:[{before, after}]}] — one entry per
    field+family that actually changed something, so the Cleansing tab can show
    exactly what a rule did before the reviewer trusts it.

    ``protected`` shields values that a rule/strategy/default deliberately set.
    """
    findings: list = []
    if df is None or len(df) == 0:
        return df, findings
    profile = profile or default_profile(df.columns)
    ascii_fold = bool(profile.get("ascii_fold"))
    out = df.copy()

    for col in out.columns:
        name = str(col)
        fams = resolve_families(name, profile)
        ovr = overrides_for(name, profile)
        if (not fams and not ovr) or not is_text_column(out[col]):
            continue
        kinds = {f: 0 for f in fams}
        examples: dict[str, list] = {f: [] for f in fams}
        ovr_count, ovr_examples = 0, []
        new_vals = []
        for raw in out[col]:
            cur = _s(raw)
            if not cur.strip():
                new_vals.append(raw)
                continue
            # An analyst override replaces the value outright and skips the rules,
            # so a correction cannot be undone by the family that caused it.
            hit = ovr.get(_norm_protect(cur))
            if hit is not None:
                if hit != cur:
                    ovr_count += 1
                    if len(ovr_examples) < 5:
                        ovr_examples.append({"before": cur, "after": hit})
                new_vals.append(hit)
                continue
            # Applied one family at a time so a change can be ATTRIBUTED to the
            # rule that made it — a combined before/after would tell the reviewer
            # something changed but not which switch to flip to stop it.
            for fam in (SPECIAL_CHARS, WHITESPACE_PUNCT, CASE, LEGAL_SUFFIX):
                if fam not in fams:
                    continue
                nxt = clean_value(cur, column=name, families=[fam],
                                  ascii_fold=ascii_fold, protected=protected)
                if nxt != cur:
                    kinds[fam] += 1
                    if len(examples[fam]) < 5:
                        examples[fam].append({"before": cur, "after": nxt})
                    cur = nxt
            new_vals.append(cur)
        out[col] = new_vals
        for fam, n in kinds.items():
            if n:
                findings.append({"field": name, "rule": fam,
                                 "label": FAMILY_LABEL[fam], "count": int(n),
                                 "examples": examples[fam]})
        if ovr_count:
            findings.append({"field": name, "rule": "override",
                             "label": "Your correction", "count": int(ovr_count),
                             "examples": ovr_examples})
    return out, findings


def preview_frame(df: pd.DataFrame, profile: Optional[dict] = None,
                  *, families: Optional[Iterable[str]] = None,
                  protected: Optional[set] = None) -> dict:
    """Dry run for the Cleansing tab: what WOULD change, without changing it.

    ``families`` overrides the profile so the UI can preview a family the user has
    not enabled yet — the whole point of the preview is deciding whether to.
    """
    prof = dict(profile or default_profile(df.columns if df is not None else []))
    if families is not None:
        prof["families"] = list(families)
        prof["per_field"] = {}
    _, findings = cleanse_frame(df, prof, protected=protected)
    return {
        "families": prof.get("families", []),
        "total_changes": int(sum(f["count"] for f in findings)),
        "fields_affected": len({f["field"] for f in findings}),
        "findings": sorted(findings, key=lambda f: -f["count"]),
    }
