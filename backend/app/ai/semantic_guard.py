"""Semantic sanity for mapping candidates.

The composite scorer ranks by token overlap, type, fill rate and so on. It has no
notion of what a column MEANS, so it will happily offer "employee_id" for a Phone
field at 25% — a number is a number. That is the noise an analyst has to wade
through, and worse, a plausible-looking wrong pick can be accepted.

This module gives each column a semantic CATEGORY inferred from its name and its
sample values, then judges whether a source category can legitimately feed a
target category. It answers the analyst's real question — "does this mapping make
sense?" — with a one-line reason, and returns a penalty the scorer can apply so
nonsense sinks to the bottom instead of masquerading as a 25% option.

Deterministic and cheap: it runs on every candidate. The optional LLM pass
(candidate_vetting_service) layers a natural-language verdict on top for the
handful of fields an analyst is actually reviewing.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ── category vocabulary ──────────────────────────────────────────────────────
# Ordered by specificity: the first matching rule wins, so "tax id" is caught
# before the generic "id" -> identifier rule.
_NAME_RULES: list[tuple[str, tuple[str, ...]]] = [
    # needles are matched AFTER "-"/"_" become spaces, so list the spaced forms
    # ("e mail" catches "E-Mail"); a bare "e-mail" needle would never match.
    ("email",       ("email", "e mail", "mail address", "remittance email", "remitemail")),
    ("url",         ("url", "website", "web site", "web address", "homepage", "http")),
    ("phone",       ("phone", "telephone", "mobile", "cell", "fax", "telex", "contact number")),
    ("tax_id",      ("tax id", "taxid", "taxpayer", "vat", "cnpj", "cpf", "ein", "tin",
                     "duns", "national insurance", "registration number")),
    ("postal_code", ("zip", "postal", "postcode", "post code")),
    ("country",     ("country", "nation")),
    ("state",       ("state", "province", "region")),
    ("city",        ("city", "town")),
    ("address",     ("address", "addr", "street", "addressee", "location line")),
    ("currency",    ("currency", "curr code", "iso currency")),
    ("money",       ("amount", "price", "cost", "limit", "balance", "credit", "salary",
                     "value", "total", "payment amount")),
    ("date",        ("date", "datetime", "timestamp", " on", "created", "updated", "inactive on")),
    ("quantity",    ("quantity", "qty", "tolerance", "count", "weight", "length", "volume",
                     "days", "period")),
    ("boolean",     ("flag", "enabled", "is ", "has ", "eligible", "hold", "active",
                     "reportable", "allow ", "yes/no")),
    ("name",        ("name", "description", "desc", "title", "label", "print as", "legal name")),
    ("code",        ("code", "type", "category", "class", "status", "method", "channel",
                     "reason", "group", "term", "lookup", "uom", "unit of measure")),
    ("identifier",  ("id", "identifier", "number", "num", "no", "key", "guid", "uuid",
                     "reference", "ref", "account", "vendor", "supplier", "customer",
                     "item", "part", "sku")),
]

# value-pattern fallbacks, used when the NAME is ambiguous
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(https?://|www\.)", re.I)
_PHONE_RE = re.compile(r"^[+(]?[\d][\d\s().\-/]{5,}$")
_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}")
_MONEY_RE = re.compile(r"^[-+]?[$€£]?\s?\d{1,3}(,\d{3})*(\.\d+)?$")
_INT_RE = re.compile(r"^[-+]?\d+$")
_ISO2_RE = re.compile(r"^[A-Za-z]{2}$")


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _name_category(name: str) -> Optional[str]:
    n = " " + _norm(name).replace("_", " ").replace("-", " ") + " "
    for cat, needles in _NAME_RULES:
        for kw in needles:
            k = kw if kw.startswith(" ") or kw.endswith(" ") else f" {kw}"
            if k.strip() and (k in n or n.strip() == kw.strip()):
                return cat
    return None


def _value_category(samples: list[str]) -> Optional[str]:
    vals = [str(v).strip() for v in (samples or []) if str(v).strip()]
    if not vals:
        return None
    def frac(rx) -> float:
        return sum(1 for v in vals if rx.search(v)) / len(vals)
    if frac(_EMAIL_RE) >= 0.6:
        return "email"
    if frac(_URL_RE) >= 0.6:
        return "url"
    if frac(_DATE_RE) >= 0.6:
        return "date"
    if all(_ISO2_RE.match(v) for v in vals) and len(vals) >= 2:
        return "country_code"
    if frac(_PHONE_RE) >= 0.6 and any(len(re.sub(r"\D", "", v)) >= 7 for v in vals):
        return "phone"
    if frac(_MONEY_RE) >= 0.6 and any(("." in v or "," in v or v[:1] in "$€£") for v in vals):
        return "money"
    return None


def classify(name: str, samples: list[str] | None = None,
             inferred_type: str | None = None) -> str:
    """Best-guess semantic category for a column. Name first (an analyst names a
    column for what it holds), then value patterns to break ambiguity."""
    by_name = _name_category(name)
    by_val = _value_category(samples or [])
    # A value signal OVERRIDES only when the name gave the generic identifier/code
    # guess — a column literally called "Phone" stays phone even if a few rows are
    # blank; but a "custom_field_12" full of e-mail addresses becomes email.
    if by_name in (None, "identifier", "code") and by_val:
        return by_val
    if by_name:
        return by_name
    if by_val:
        return by_val
    return "unknown"


# ── compatibility ────────────────────────────────────────────────────────────
# Categories that must NOT feed each other. Symmetric; only genuinely nonsensical
# pairs are listed, so the guard demotes noise without blocking legitimate but
# unusual mappings (which the scorer + analyst still handle).
_INCOMPATIBLE: dict[str, set[str]] = {
    "phone":       {"identifier", "tax_id", "money", "date", "email", "name", "postal_code",
                    "country", "country_code", "currency", "quantity", "boolean", "url", "code"},
    "email":       {"identifier", "tax_id", "money", "date", "phone", "postal_code",
                    "country", "country_code", "currency", "quantity", "boolean"},
    "date":        {"identifier", "tax_id", "money", "phone", "email", "name", "postal_code",
                    "country", "country_code", "currency", "boolean", "url", "code"},
    "money":       {"identifier", "tax_id", "phone", "email", "date", "name", "postal_code",
                    "country", "country_code", "boolean", "url"},
    "currency":    {"identifier", "tax_id", "phone", "email", "date", "money", "name",
                    "address", "quantity", "url"},
    "country_code": {"phone", "email", "date", "money", "name", "address", "quantity", "url"},
    "url":         {"identifier", "tax_id", "phone", "email", "date", "money", "postal_code",
                    "country", "currency", "quantity", "boolean"},
    "boolean":     {"identifier", "tax_id", "phone", "email", "date", "money", "name",
                    "address", "url", "postal_code"},
}

_LABEL = {
    "identifier": "an identifier", "tax_id": "a tax/registration id", "phone": "a phone number",
    "email": "an e-mail address", "url": "a web address", "date": "a date", "money": "a monetary amount",
    "currency": "a currency code", "country": "a country", "country_code": "a 2-letter country code",
    "postal_code": "a postal code", "state": "a state/province", "city": "a city",
    "address": "an address", "name": "a name/description", "code": "a coded value",
    "quantity": "a quantity", "boolean": "a yes/no flag", "unknown": "an unrecognised value",
}


def compatibility(src_cat: str, tgt_cat: str) -> tuple[bool, str]:
    """Can a source of src_cat legitimately feed a target of tgt_cat?

    Returns (plausible, reason). Only clearly nonsensical pairs are rejected;
    everything else is allowed and left to the score + the analyst.
    """
    if src_cat == "unknown" or tgt_cat == "unknown":
        return True, ""
    if src_cat == tgt_cat:
        return True, f"both are {_LABEL.get(src_cat, src_cat)}"
    bad = _INCOMPATIBLE.get(tgt_cat, set())
    if src_cat in bad or tgt_cat in _INCOMPATIBLE.get(src_cat, set()):
        return False, (f"source looks like {_LABEL.get(src_cat, src_cat)}, but this field "
                       f"expects {_LABEL.get(tgt_cat, tgt_cat)}")
    return True, ""


def vet_candidate(src_name: str, src_samples: list[str] | None,
                  tgt_name: str, tgt_desc: str | None = None) -> dict:
    """Deterministic verdict for one (source, target) pair.

    { plausible, penalty (0..1), source_category, target_category, reason }.
    penalty is applied to the composite score so an implausible pair sinks.
    """
    sc = classify(src_name, src_samples)
    tc = classify(tgt_name + " " + (tgt_desc or ""))
    ok, reason = compatibility(sc, tc)
    return {
        "plausible": ok,
        "penalty": 0.0 if ok else 0.35,
        "source_category": sc,
        "target_category": tc,
        "reason": reason,
    }
