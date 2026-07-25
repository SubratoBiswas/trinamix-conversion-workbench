"""Synthetic test-data generation for load rehearsals & demos.

Given an Oracle FBDI interface's field metadata, generate N rows of realistic,
type-valid sample values so an analyst can rehearse a load (or demo the tool)
without real client data. Values honour: required flags, data type + max length,
published lists-of-values (allowed_values codes), date format masks, and column-name
heuristics (names, emails, countries, currencies, phones, identifiers). Business-key
columns get unique sequential values.

Pure (stdlib + pandas) and seeded for reproducibility, so it is unit-testable.
"""
from __future__ import annotations

import random
import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd

_COMPANIES = ["Acme", "Globex", "Initech", "Umbrella", "Soylent", "Stark", "Wayne",
              "Wonka", "Hooli", "Vandelay", "Cyberdyne", "Tyrell", "Pied Piper", "Nakatomi"]
_SUFFIX = ["Inc", "LLC", "Corp", "Ltd", "Co", "Group", "Industries"]
_FIRST = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Sam", "Jamie", "Chris", "Pat"]
_LAST = ["Smith", "Johnson", "Lee", "Patel", "Garcia", "Nguyen", "Brown", "Khan", "Silva", "Wang"]
_CITIES = ["Boston", "Denver", "Austin", "Seattle", "Chicago", "Dallas", "Miami", "Phoenix"]


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _is_keyish(name: str) -> bool:
    n = _norm(name)
    return (("number" in n and "phone" not in n and "fax" not in n)
            or n.endswith("id") or "identifier" in n or "batchid" in n)


def _gen_value(field: dict, i: int, rng: random.Random):
    name = field.get("field_name") or field.get("display_name") or ""
    n = _norm(name)
    dtype = (field.get("data_type") or "").lower()
    maxlen = field.get("max_length") or 0
    allowed = [a.get("code") for a in (field.get("allowed_values") or [])
               if a.get("code") not in (None, "")]

    if allowed:
        return str(rng.choice(allowed))

    # Business keys → unique sequential
    if _is_keyish(name):
        base = 100000 + i
        return str(base) if ("number" in n or n.endswith("id") or "identifier" in n) else f"K{base}"

    if "date" in dtype or "date" in n:
        d = date(2024, 1, 1) + timedelta(days=rng.randint(0, 700))
        mask = (field.get("format_mask") or "YYYYMMDD").upper()
        return d.strftime("%Y%m%d") if "YYYYMMDD" in mask else d.isoformat()

    if dtype.startswith(("number", "integer", "float", "decimal")):
        digits = min(6, maxlen or 6) if maxlen else 6
        return str(rng.randint(1, 10 ** max(1, digits - 1)))

    # Character heuristics
    if "email" in n:
        return f"user{i}@example.com"
    if "supplier" in n and "name" in n or "vendor" in n and "name" in n or n in ("name", "partyname", "organizationname", "customername"):
        v = f"{rng.choice(_COMPANIES)} {rng.choice(_SUFFIX)}"
    elif "firstname" in n:
        v = rng.choice(_FIRST)
    elif "lastname" in n:
        v = rng.choice(_LAST)
    elif "name" in n:
        v = f"{rng.choice(_COMPANIES)} {rng.choice(_SUFFIX)}"
    elif "country" in n:
        v = "US"
    elif "currency" in n:
        v = "USD"
    elif "city" in n:
        v = rng.choice(_CITIES)
    elif "phone" in n or "fax" in n:
        v = f"{rng.randint(200,999)}{rng.randint(200,999)}{rng.randint(1000,9999)}"
    elif "postal" in n or n == "zip":
        v = str(rng.randint(10000, 99999))
    elif "code" in n:
        v = f"{rng.choice('ABCDEFGH')}{rng.randint(10,99)}"
    elif "description" in n:
        v = f"Sample {rng.choice(_COMPANIES)} record {i}"
    else:
        # generic required-safe token
        token = re.sub(r"[^A-Za-z0-9]", "", name)[:8] or "VAL"
        v = f"{token}_{i}"
    if maxlen and len(v) > maxlen:
        v = v[:maxlen]
    return v


def synthetic_frame(fields: list[dict], n: int = 25, *, seed: int = 42,
                    optional_fill: float = 0.5) -> pd.DataFrame:
    """Generate ``n`` synthetic rows for the given fields. Required fields are always
    populated; optional fields are populated ~``optional_fill`` of the time (or always
    when they carry a list-of-values). Columns are keyed by ``field_name``."""
    if not fields:
        return pd.DataFrame()
    rng = random.Random(seed)
    # de-dup column names, keep order
    seen: set = set()
    cols = []
    for f in fields:
        c = f.get("field_name") or f.get("display_name")
        if c and c not in seen:
            seen.add(c)
            cols.append(f)
    rows = []
    for i in range(n):
        row = {}
        for f in cols:
            name = f.get("field_name") or f.get("display_name")
            required = bool(f.get("required"))
            has_lov = bool(f.get("allowed_values"))
            if required or has_lov or rng.random() < optional_fill:
                row[name] = _gen_value(f, i, rng)
            else:
                row[name] = ""
        rows.append(row)
    return pd.DataFrame(rows, columns=[f.get("field_name") or f.get("display_name") for f in cols])
