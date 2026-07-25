"""Source-data anomaly / outlier detection — run on a source extract BEFORE
mapping to surface the data problems that later cause FBDI load failures.

Deterministic column profiling (no DB, network or model) that flags: high null
rate, leading/trailing whitespace, mixed types (numbers + text in one column),
numeric outliers (IQR), inconsistent casing / whitespace variants of the same
value, embedded units ("5 kg", "$10", "10%"), value-length outliers, non-printable
characters, and fully duplicated rows. Each finding carries a severity, a count,
and real example values so the analyst can act before mapping.

An OPTIONAL AI pass (``ai_review_anomalies``) can add a plain-English risk note per
finding via the configured LLM; it degrades to the deterministic findings when AI
is unavailable. Kept dependency-light (pandas + stdlib) so it is unit-testable.
"""
from __future__ import annotations

import re
from typing import Optional

import pandas as pd

_NUMERIC_RE = re.compile(r"^-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?%?$|^-?\d*\.?\d+$")
_EMBEDDED_UNIT_RE = re.compile(
    r"^\s*-?\$?\d[\d,]*\.?\d*\s*(kg|g|lb|lbs|oz|mm|cm|m|in|ft|%|usd|eur|gbp|pcs|ea|units?|hrs?|days?)\b",
    re.IGNORECASE)
_NONPRINT_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")

SEV_ORDER = {"error": 3, "warning": 2, "info": 1}


def _is_numeric(s: str) -> bool:
    return bool(_NUMERIC_RE.match(s.strip()))


def _num(s: str) -> Optional[float]:
    try:
        return float(re.sub(r"[,$%\s]", "", s))
    except Exception:  # noqa: BLE001
        return None


def _norm_val(s: str) -> str:
    return _WS.sub(" ", str(s).strip().lower())


def _finding(col, itype, sev, count, total, examples, detail):
    return {
        "column": col, "issue_type": itype, "severity": sev,
        "count": int(count), "pct": round(count / total, 4) if total else 0.0,
        "examples": examples[:5], "detail": detail,
    }


def detect_anomalies(df: pd.DataFrame, *, max_examples: int = 5,
                     categorical_max: int = 80) -> dict:
    """Profile every column and return anomaly findings. Result:
    ``{rows, columns_scanned, findings:[...], summary:{error,warning,info,columns_flagged}}``."""
    if df is None or df.empty:
        return {"rows": 0, "columns_scanned": 0, "findings": [], "duplicate_rows": 0,
                "summary": {"error": 0, "warning": 0, "info": 0, "columns_flagged": 0}}
    n = len(df)
    findings: list[dict] = []

    for col in df.columns:
        ser = df[col]
        s = ser.astype(str)
        # non-blank mask
        nonblank = ser.notna() & (s.str.strip() != "") & (s.str.lower() != "nan")
        vals = s[nonblank]
        filled = int(nonblank.sum())
        nulls = n - filled

        # 1. High null rate
        if n and nulls / n >= 0.4:
            sev = "error" if nulls / n >= 0.7 else "warning"
            findings.append(_finding(col, "High null rate", sev, nulls, n, [], f"{nulls}/{n} rows are blank."))

        if filled == 0:
            continue

        # 2. Leading / trailing whitespace
        ws = vals[vals != vals.str.strip()]
        if len(ws):
            findings.append(_finding(col, "Leading/trailing spaces", "warning", len(ws), filled,
                                     [repr(x) for x in ws.head(max_examples)], "Values padded with spaces will mismatch on load."))

        stripped = vals.str.strip()
        # numeric fraction
        num_mask = stripped.map(_is_numeric)
        num_frac = float(num_mask.mean())

        # 3. Mixed types (both numbers and text meaningfully present)
        if 0.15 <= num_frac <= 0.85 and stripped.nunique() > 1:
            offenders_text = stripped[~num_mask].head(max_examples).tolist()
            findings.append(_finding(col, "Mixed types (numbers + text)", "warning",
                                     int((~num_mask).sum() if num_frac >= 0.5 else num_mask.sum()),
                                     filled, offenders_text,
                                     f"{round(num_frac*100)}% of values are numeric, the rest text."))

        # 4. Numeric outliers (IQR) for mostly-numeric columns
        if num_frac >= 0.9:
            nums = stripped[num_mask].map(_num).dropna()
            if len(nums) >= 8 and nums.nunique() > 3:
                q1, q3 = nums.quantile(0.25), nums.quantile(0.75)
                iqr = q3 - q1
                if iqr > 0:
                    lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                    out = nums[(nums < lo) | (nums > hi)]
                    if len(out):
                        findings.append(_finding(col, "Numeric outliers", "info", len(out), filled,
                                                 [str(x) for x in out.head(max_examples)],
                                                 f"Values outside [{round(lo,2)}, {round(hi,2)}] (3×IQR)."))

        # 5. Embedded units in an otherwise numeric column
        unit_hits = stripped[stripped.map(lambda x: bool(_EMBEDDED_UNIT_RE.match(x)))]
        if len(unit_hits) and len(unit_hits) < filled:
            findings.append(_finding(col, "Embedded units", "warning", len(unit_hits), filled,
                                     unit_hits.head(max_examples).tolist(),
                                     "Numbers carry a unit suffix; strip the unit before load."))

        # 6. Inconsistent casing / whitespace variants of the SAME value
        if stripped.nunique() <= categorical_max:
            grp: dict[str, set] = {}
            for v in stripped.unique():
                grp.setdefault(_norm_val(v), set()).add(v)
            variant_groups = {k: v for k, v in grp.items() if len(v) > 1}
            if variant_groups:
                ex = ["/".join(sorted(list(v))[:3]) for v in list(variant_groups.values())[:max_examples]]
                affected = int(stripped.isin({x for v in variant_groups.values() for x in v}).sum())
                findings.append(_finding(col, "Inconsistent casing/spacing", "warning", len(variant_groups), filled,
                                         ex, "Same value written multiple ways — normalize to one form."))

        # 7. Non-printable / control characters
        bad = vals[vals.map(lambda x: bool(_NONPRINT_RE.search(x)))]
        if len(bad):
            findings.append(_finding(col, "Non-printable characters", "error", len(bad), filled,
                                     [repr(x) for x in bad.head(max_examples)], "Control characters can break the CSV load."))

        # 8. Value-length outliers (a few unusually long/short)
        if stripped.nunique() > 5:
            lens = stripped.str.len()
            med = lens.median()
            if med and med > 0:
                far = stripped[(lens > med * 4) & (lens > med + 10)]
                if 0 < len(far) <= max(3, int(0.05 * filled)):
                    findings.append(_finding(col, "Value-length outliers", "info", len(far), filled,
                                             [x[:40] + ("…" if len(x) > 40 else "") for x in far.head(max_examples)],
                                             f"A few values are far longer than the typical {int(med)} chars."))

    dup_rows = int(df.duplicated().sum())
    if dup_rows:
        findings.append(_finding("(row)", "Duplicate rows", "warning", dup_rows, n, [], f"{dup_rows} fully duplicated rows."))

    findings.sort(key=lambda f: (-SEV_ORDER.get(f["severity"], 0), -f["pct"]))
    summary = {
        "error": sum(1 for f in findings if f["severity"] == "error"),
        "warning": sum(1 for f in findings if f["severity"] == "warning"),
        "info": sum(1 for f in findings if f["severity"] == "info"),
        "columns_flagged": len({f["column"] for f in findings}),
    }
    return {"rows": n, "columns_scanned": len(df.columns), "duplicate_rows": dup_rows,
            "findings": findings, "summary": summary}


async def ai_review_anomalies(result: dict, object_hint: Optional[str] = None) -> dict:
    """Optional: add a short AI 'risk' note to the top findings via the configured
    LLM. Best-effort — leaves findings unchanged (``ai_used=False``) on any failure."""
    result = dict(result)
    result["ai_used"] = False
    findings = result.get("findings") or []
    top = findings[:12]
    if not top:
        return result
    try:
        import json
        import httpx
        from app.config import settings
        provider = (settings.AI_PROVIDER or "none").lower()
        if provider not in ("anthropic", "openai"):
            return result
        payload = [{"id": i, "column": f["column"], "issue": f["issue_type"],
                    "examples": f["examples"]} for i, f in enumerate(top)]
        prompt = (
            "You are an Oracle Fusion data-migration data-quality expert. For each "
            f"anomaly below (from a source file for the '{object_hint or 'Fusion'}' "
            "object), give a one-sentence RISK explaining how it could fail the FBDI "
            "load and the fix. Return ONLY a JSON array "
            '[{"id":<id>,"risk":"..."}].\n\nANOMALIES:\n' + json.dumps(payload, indent=1))
        if provider == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages",
                           headers={"x-api-key": settings.ANTHROPIC_API_KEY,
                                    "anthropic-version": "2023-06-01", "content-type": "application/json"},
                           json={"model": settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
                                 "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]},
                           timeout=50.0)
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        else:
            r = httpx.post("https://api.openai.com/v1/chat/completions",
                           headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                           json={"model": settings.OPENAI_MODEL, "messages": [{"role": "user", "content": prompt}]},
                           timeout=50.0)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        text = text.strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        notes = {int(x["id"]): x.get("risk", "") for x in json.loads(text) if "id" in x}
        for i, f in enumerate(top):
            if notes.get(i):
                f["ai_risk"] = notes[i]
        result["ai_used"] = True
    except Exception:  # noqa: BLE001
        return result
    return result
