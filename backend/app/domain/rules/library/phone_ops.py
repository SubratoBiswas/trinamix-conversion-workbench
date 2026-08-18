"""Phone rule strategies migrated out of engine._apply_one_rule. The libphonenumber
split + region lookup now live in app.domain.phone.parse; PhonePartRule composes them.
PhoneStripAreaRule is self-contained (digits-only prefix strip). Each reproduces its
former branch VERBATIM (``cfg`` -> ``config``)."""
from __future__ import annotations
import re
from typing import Any

from app.domain.text import to_str as _to_str
from app.domain.phone.parse import phone_split as _phone_split, phone_region_for as _phone_region_for


class PhoneStripAreaRule:
    rule_type = "PHONE_STRIP_AREA"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        # Oracle stores Area Code and Phone Number in SEPARATE columns. When the
        # extract already has the area code in its own column, leaving it on the
        # front of the number duplicates it (e.g. area 512 + number "512-555-0134"
        # loads as "512 512-555-0134"). Strip it — but ONLY when the number really
        # begins with that area code, so a number that was already clean, or one
        # that happens to start with the same digits by coincidence of formatting,
        # is left alone. Digits-only comparison, original formatting preserved on
        # whatever remains. cfg: {"area_code_column": "<name>"}.
        col = cfg.get("area_code_column")
        raw = _to_str(value).strip()
        if row is None or not col or not raw:
            return value
        area = "".join(ch for ch in _to_str(row.get(col, "")) if ch.isdigit())
        if not area:
            return value
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits.startswith(area) or len(digits) <= len(area):
            return value          # not a duplicated prefix — leave untouched
        # Walk the original string and drop the leading separators + area digits.
        seen = 0
        for i, ch in enumerate(raw):
            if ch.isdigit():
                seen += 1
                if seen == len(area):
                    return raw[i + 1:].lstrip(" -.()/").strip()
        return value


class PhonePartRule:
    rule_type = "PHONE_PART"
    def apply(self, value: Any, config: dict, row=None, ctx=None) -> Any:
        cfg = config
        # Split a single phone/fax string into its Oracle parts. Handles the common
        # legacy forms: "+91 22 1234567", "+1 (415) 555-0100 x23", "0044-20-7946-0000".
        # config: {"part": "country" | "area" | "number" | "extension"}. Deterministic
        # (no per-format regex config needed); unknown/degenerate inputs return "".
        part = (cfg.get("part") or "number").lower()
        raw = _to_str(value).strip()
        if not raw:
            return ""
        # PRIMARY: libphonenumber, with the row's Country column as the region hint.
        # This is what lets a bare "5515981205351" (no + / no separators) be split
        # into +55 / 15 / 981205351 instead of dumping the whole string into the
        # "number" part — the reported 10-Aug defect. Only used when it yields a
        # parseable number; otherwise the legacy tokeniser below runs unchanged, so
        # an unparseable value never regresses.
        _split = _phone_split(raw, _phone_region_for(row))
        if _split is not None:
            return _split.get(part, "")
        # FALLBACK (legacy tokeniser): no region and no international prefix, or an
        # unparseable value. 1) pull an extension off the end, if any.
        ext = ""
        mext = re.search(r"(?i)(?:ext|extn|extension|x)\.?\s*(\d{1,6})\s*$", raw)
        if mext:
            ext = mext.group(1)
            raw = raw[:mext.start()].strip()
        if part == "extension":
            return ext
        has_plus = raw.lstrip().startswith("+") or raw.lstrip().startswith("00")
        # 2) tokenize into digit groups (preserving order); a leading 00 is an
        # international prefix, treat like '+'.
        body = raw.lstrip()
        if body.startswith("00"):
            body = body[2:]
            has_plus = True
        groups = re.findall(r"\d+", body)
        if not groups:
            return ""
        country = area = ""
        rest = list(groups)
        if has_plus:
            country = rest.pop(0)
        if part == "country":
            return country
        # area code = the next group when there are still >=2 groups left (so a
        # bare local number isn't misread as an area code).
        if len(rest) >= 2:
            area = rest.pop(0)
        if part == "area":
            return area
        # number = whatever remains, concatenated.
        return "".join(rest)
