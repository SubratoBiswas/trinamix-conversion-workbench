"""The FBDI date value object — Phase 1a of the clean-architecture migration.

This is the single, pure home for date parsing/formatting. It is being introduced
BEHIND the existing functions (customer_merge._fbdi_date first) so the four historical
parsers can be collapsed one at a time WITHOUT changing a single output byte:

  * customer_merge._fbdi_date      -> from_regex  (non-validating, prefix-anchored)   [1a]
  * output_service.to_fbdi_date    -> from_formats (strptime, validating)             [1b]
  * engine._parse_any_date         -> from_formats                                     [1b]
  * engine._norm_date              -> from_formats                                     [1b]

`from_regex` is deliberately NON-VALIDATING: the legacy _fbdi_date built its result
with a regex and f-strings, so it could (and did) emit an impossible month like
``2018/20/08`` when a day-first value was read month-first. Reproducing that exactly is
what makes the delegation byte-identical; correctness of the *order* is a separate,
already-shipped concern (DateOrder), not something this refactor is allowed to change.

No framework imports. Unit-testable in isolation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# Values a source cell can carry that mean "blank". This is the EXACT set the legacy
# customer_merge._fbdi_date used; kept as a named constant so a caller can reproduce its
# own contract precisely rather than inherit a well-meaning superset.
FBDI_NA = ("nan", "none", "null", "nat", "na", "<na>")

_ISO_PREFIX = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DMY_PREFIX = re.compile(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{4})")


class DateOrder(Enum):
    """How a two-fields-then-year value (``08-11-2018``) is read. A property of the
    SOURCE export locale (NetSuite/NextPower = DAY_FIRST). MONTH_FIRST is the historical
    default and keeps every non-day-first source byte-identical."""
    MONTH_FIRST = "month_first"
    DAY_FIRST = "day_first"


@dataclass(frozen=True)
class FbdiDate:
    """A calendar triple. Deliberately NON-VALIDATING — it may hold month=20 — because
    the regex date path it replaces did. Validity is queryable via ``is_valid`` for the
    strptime-based callers that need it, but construction never rejects."""
    year: int
    month: int
    day: int

    def to_fbdi(self, sep: str = "/") -> str:
        """``yyyy<sep>mm<sep>dd``. sep='/' for FBDI columns; '-' only where a template
        explicitly asks — a single decision instead of scattered string-building."""
        return f"{self.year:04d}{sep}{self.month:02d}{sep}{self.day:02d}"

    @property
    def is_valid(self) -> bool:
        try:
            datetime(self.year, self.month, self.day)
            return True
        except ValueError:
            return False

    @classmethod
    def from_regex(cls, s: str, order: DateOrder = DateOrder.MONTH_FIRST) -> "FbdiDate | None":
        """Non-validating, prefix-anchored parse — the exact strategy the legacy
        customer_merge._fbdi_date used. A YYYY-first value is unambiguous; a
        ``dd?-mm?-yyyy`` value is read day/month by ``order``. Returns None when neither
        prefix matches, so the caller can preserve its own miss behaviour."""
        if (m := _ISO_PREFIX.match(s)):
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if (m := _DMY_PREFIX.match(s)):
            a, b = int(m.group(1)), int(m.group(2))
            day, month = (a, b) if order is DateOrder.DAY_FIRST else (b, a)
            return cls(int(m.group(3)), month, day)
        return None


def fbdi_date(v: object, dayfirst: bool = False) -> str:
    """Byte-for-byte replacement for ``customer_merge._fbdi_date``.

    blank / NA          -> ``""``
    YYYY-first          -> ``yyyy/mm/dd``
    dd?-mm?-yyyy        -> ``yyyy/mm/dd`` (day/month order by ``dayfirst``; NON-validating)
    anything else       -> the stripped original string
    """
    s = str(v or "").strip()
    if not s or s.lower() in FBDI_NA:
        return ""
    order = DateOrder.DAY_FIRST if dayfirst else DateOrder.MONTH_FIRST
    d = FbdiDate.from_regex(s, order)
    return d.to_fbdi("/") if d else s


# ── Phase 1b: the strptime-validating parsers ───────────────────────────────────
# The three remaining historical parsers (output_service.to_fbdi_date,
# engine._parse_any_date, engine._norm_date) share one strategy: try a list of strptime
# formats in order, first that parses wins. The loop and the format lists are relocated
# here verbatim so ALL date-format knowledge lives in one module; each caller keeps its
# own list identity and its own blank/miss/token wrapper, so behaviour is byte-identical.

# engine._parse_any_date  (returns a datetime; month-first default + day-first variant)
PARSE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%d/%m/%Y", "%m-%d-%Y", "%d-%m-%Y",
    "%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y",
)
PARSE_FORMATS_DAYFIRST = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y",
    "%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d-%B-%Y",
)

# output_service.to_fbdi_date  (blanket date-column pass; wider set incl. compact + names)
INPUT_FORMATS = (
    "%Y%m%d",
    "%Y-%m-%d", "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%m-%d-%Y",
    "%d/%m/%Y", "%d-%m-%Y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%b %d, %Y",
)
INPUT_FORMATS_DAYFIRST = (
    "%Y%m%d",
    "%Y-%m-%d", "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y",
    "%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%m-%d-%Y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%b %d, %Y",
)

# engine CONDITIONAL_DATE._norm_date  (token/other-column dates)
CONDITIONAL_FORMATS = (
    "%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
    "%Y%m%d", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)


def parse_with_formats(s: str, formats) -> "datetime | None":
    """Try each strptime format in order; the first that parses wins, else None. The
    single shared core of the three validating parsers. The caller pre-processes the
    string (blank guard, fractional-second strip, token handling) and post-processes the
    result (its own output format / miss return), so its exact contract is preserved."""
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None
