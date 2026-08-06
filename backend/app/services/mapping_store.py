"""The one dated store.

Agreed with the analyst, 02-Aug-2026:

    "Mappings, learnings and user inputs should be stored in the same place with
    date (with respect to client and source), whichever is latest as per date the
    mapping will happen in that way, and the same will be used for existing
    projects and future projects."

And, on per-conversion overrides:

    "That's fine, the analyst mapping wins as that's the latest mapping as per
    date."

So: every statement about how a field should be mapped is a dated entry, keyed
``(client_id, source_erp, target_field)``. The mapping workbook, the gold
standard, a captured learning, the steer box, a grid edit and a custom rule all
write entries of the same shape. **The newest one wins.** Who wrote it is
provenance, recorded so a human can trace the decision — it is never precedence.

There is no object scope, no project scope and no per-conversion override. An
edit is the client's newest statement about that field and it applies
everywhere, to projects that already exist and to projects not yet created.

Why it is one store, and why the key is that short: there used to be two stores —
``LearnedMapping`` (dated, client+source scoped) and ``MappingSuggestion`` (the
per-conversion rows, which generation actually read) — and the library was
COPIED into the rows. Every "the screen says one thing and the file says
another" bug came out of those two disagreeing, and each extra scope was another
axis they could disagree on. Suppression-loses-to-mapping, most-specific-wins,
object-key spelling drift and the six-call-sites fan-out were all precedence
tiers competing with the date. They are gone. One key, one date, one winner.

This module is the whole of that rule. It is pure — it takes entries and answers
questions about them — so it can be tested against a table of competing entries
without a database.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as _dc_field
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterable, Optional, Sequence

# ── What a decision can say ──────────────────────────────────────────────────
#
# Four shapes, and every writer produces one of them. `value` means something
# slightly different in each, which is why it is read through `value_of` rather
# than off the row.
SOURCE_COLUMN = "source_column"   # read this source column
DEFAULT_VALUE = "default_value"   # write this constant
SUPPRESS = "suppress"             # ship this field empty
RULE = "rule"                     # run this transformation rule

DECISIONS = (SOURCE_COLUMN, DEFAULT_VALUE, SUPPRESS, RULE)

# The stored `kind` is the old vocabulary. It stays on the row because the
# Learning Centre, the seeders and the analyst's own JSON all speak it; the
# decision is what the resolver reasons about.
KIND_TO_DECISION: dict[str, str] = {
    "column_mapping": SOURCE_COLUMN,
    "example_default": DEFAULT_VALUE,
    "suppress_field": SUPPRESS,
    "rule": RULE,
}
DECISION_TO_KIND: dict[str, str] = {v: k for k, v in KIND_TO_DECISION.items()}

# Kinds that are statements about how a field maps, and therefore belong in this
# store. The others on LearnedMapping are not mapping decisions and are left
# alone: `crosswalk` is one row per source VALUE, `file_signature` identifies an
# uploaded file, `ignore_source` is about a source column rather than a target
# field, and `reference_standard` records a master-key format.
DECISION_KINDS = frozenset(KIND_TO_DECISION)

log = logging.getLogger(__name__)

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def normalise_field(name: str | None) -> str:
    """The key form of a target field name.

    Oracle spells the same field ``Party Original System Reference``,
    ``PartyOriginalSystemReference`` and ``PARTY_ORIGINAL_SYSTEM_REFERENCE``
    across the workbook, the template and the analyst's mail. They are one
    field, so they are one key.
    """
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", str(name).lower())


def normalise_source(name: str | None) -> str:
    """The key form of a source system name. ``NetSuite`` and ``netsuite`` are one."""
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", str(name).lower())


def client_key(client_id: Any) -> str:
    """The key form of a client id. ``None`` means "every client"."""
    return "" if client_id is None else str(client_id)


# ── The entry ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Entry:
    """One dated statement about one field.

    ``effective_date`` is WHEN THE INSTRUCTION WAS GIVEN, which is not when the
    row was written. It is the ordering key and the only thing that decides a
    contest. It must never move on a read — ``captured_at`` did exactly that to
    the learnings layer, because every startup seed re-stamped it, and that
    inverted precedence on every redeploy.

    ``captured_from`` and ``captured_by`` are provenance. Nothing reads them to
    decide a winner.
    """

    target_field: str
    decision: str
    value: Optional[str] = None
    client_id: Any = None
    source_erp: Optional[str] = None
    effective_date: Optional[datetime] = None
    captured_from: Optional[str] = None
    captured_by: Optional[str] = None
    rule_type: Optional[str] = None
    rule_config: dict = _dc_field(default_factory=dict)
    sheets: tuple[str, ...] = ()
    exclude_sheets: tuple[str, ...] = ()
    is_deleted: bool = False
    # Provenance only. The object a decision happened to be captured under is
    # recorded so a human can see where it came from; it is NOT part of the key
    # and it never narrows where the decision applies.
    target_object: Optional[str] = None
    # The underlying document, when the entry came from one. Callers that need
    # to write back (bump a counter, retire a row) reach it through here.
    row: Any = None

    @property
    def field_key(self) -> str:
        return normalise_field(self.target_field)

    @property
    def key(self) -> tuple[str, str, str]:
        """``(client, source, field)`` — the whole of the identity."""
        return (client_key(self.client_id),
                normalise_source(self.source_erp),
                normalise_field(self.target_field))


def effective_of(row: Any) -> datetime:
    """When this entry's INSTRUCTION was given.

    Falls back to ``captured_at``, which for something typed into the UI IS the
    moment the instruction was given. An entry that carries neither counts as
    older than everything: it cannot be shown to have come later, and reading an
    undated decision as newer is what made corrections vanish.
    """
    for attr in ("effective_date", "captured_at"):
        val = getattr(row, attr, None) if not isinstance(row, dict) else row.get(attr)
        if isinstance(val, datetime):
            return val
    return datetime.min


def value_of(row: Any) -> Optional[str]:
    """What this entry says to write, in the terms of its decision.

    - ``source_column`` → the source column name (stored as ``original_value``)
    - ``default_value`` → the constant (``rule_config.default_value``, else
      ``resolved_value``)
    - ``suppress``      → nothing; the field ships empty
    - ``rule``          → the source column the rule reads, when it reads one
    """
    kind = getattr(row, "kind", None)
    decision = KIND_TO_DECISION.get(kind or "")
    cfg = getattr(row, "rule_config", None) or {}
    if decision == DEFAULT_VALUE:
        val = cfg.get("default_value")
        if val is None or str(val).strip() == "":
            val = getattr(row, "resolved_value", None)
        return None if val is None else str(val)
    if decision == SUPPRESS:
        return None
    orig = getattr(row, "original_value", None)
    return None if orig is None else str(orig)


def value_for(kind: str | None, *, original_value: Any = None,
              resolved_value: Any = None, rule_config: dict | None = None
              ) -> Optional[str]:
    """``value_of`` for fields that are not on a row yet — what a writer holds."""
    return value_of(SimpleNamespace(kind=kind, original_value=original_value,
                                    resolved_value=resolved_value,
                                    rule_config=rule_config or {}))


def entry_of(row: Any) -> Optional[Entry]:
    """Turn a stored ``LearnedMapping`` into an entry, or ``None`` if it is not
    a statement about how a field maps."""
    kind = getattr(row, "kind", None)
    decision = KIND_TO_DECISION.get(kind or "")
    if not decision:
        return None
    target_field = getattr(row, "target_field", None)
    if not target_field:
        return None
    return Entry(
        target_field=str(target_field),
        decision=decision,
        value=value_of(row),
        client_id=getattr(row, "client_id", None),
        source_erp=getattr(row, "source_erp", None),
        effective_date=effective_of(row),
        captured_from=getattr(row, "captured_from", None),
        captured_by=getattr(row, "captured_by", None),
        rule_type=getattr(row, "rule_type", None),
        rule_config=dict(getattr(row, "rule_config", None) or {}),
        sheets=tuple(s for s in (getattr(row, "sheets", None) or []) if str(s).strip()),
        exclude_sheets=tuple(
            s for s in (getattr(row, "exclude_sheets", None) or []) if str(s).strip()),
        is_deleted=bool(getattr(row, "is_deleted", False)),
        target_object=getattr(row, "target_object", None),
        row=row,
    )


def entries_of(rows: Iterable[Any]) -> list[Entry]:
    return [e for e in (entry_of(r) for r in rows) if e is not None]


# ── Applicability ────────────────────────────────────────────────────────────

def sheet_allowed(entry_or_row: Any, sheet_name: str | None) -> bool:
    """May this entry touch this interface sheet?

    Not a precedence scope — it does not create a tier that competes with the
    date. It is part of what the analyst SAID: Oracle repeats a field name
    across sheets (Customer has 19), and "id maps to Party Original System
    Reference, but not on HZ_IMP_CLASSIFICS_T" is one instruction, not two
    ranked ones. Among the entries that apply to a sheet, the newest still wins
    outright.

    Empty lists mean every sheet, which is what every entry captured before this
    existed means. Exclusion beats inclusion: naming a sheet under "never" is
    the stronger statement.
    """
    only = [s for s in (getattr(entry_or_row, "sheets", None) or []) if str(s).strip()]
    never = [s for s in (getattr(entry_or_row, "exclude_sheets", None) or [])
             if str(s).strip()]
    if not only and not never:
        return True
    name = normalise_field(sheet_name)
    if not name:
        # Unknown sheet: allow when the entry only EXCLUDES — nothing says this
        # is the excluded one — and refuse when it names an allow-list this
        # sheet cannot be shown to be part of.
        return not only
    if any(normalise_field(s) == name for s in never):
        return False
    return not only or any(normalise_field(s) == name for s in only)


def applies(entry: Entry, *, client_id: Any = None, source_erp: str | None = None,
            target_field: str | None = None, sheet: str | None = None) -> bool:
    """Is this entry a statement about the thing being asked about?

    Client and source narrow, because they are facts about whose data this is
    and where it came from — the same target field is fed by a different column
    depending on which legacy system the extract came from. An entry that names
    neither is a statement about every client and every source; that is what the
    whole existing library is, so it still counts.

    Nothing else narrows. There is deliberately no object test here.
    """
    if entry.is_deleted:
        return False
    if target_field is not None:
        if normalise_field(entry.target_field) != normalise_field(target_field):
            return False
    if entry.client_id is not None and client_id is not None:
        if client_key(entry.client_id) != client_key(client_id):
            return False
    if entry.source_erp and source_erp:
        if normalise_source(entry.source_erp) != normalise_source(source_erp):
            return False
    return sheet_allowed(entry, sheet)


def _order(entry: Entry, *, client_id: Any, source_erp: str | None) -> tuple:
    """Sort key. Latest first — everything after the date is only there so that
    two entries bearing the SAME instant resolve the same way every time.

    The tie-breaks are not authority. They prefer the statement that names this
    client and this source over one that names neither, because a statement
    about everybody was never meant to overrule a statement about you made at
    the same moment; and then they fall back to a stable string so the answer
    cannot flip between two runs over the same data.
    """
    exact_client = 0 if (entry.client_id is not None and client_id is not None
                         and client_key(entry.client_id) == client_key(client_id)) else 1
    exact_source = 0 if (entry.source_erp and source_erp
                         and normalise_source(entry.source_erp)
                         == normalise_source(source_erp)) else 1
    # A keep-blank and a fixed value dated the SAME instant are the analyst saying
    # two things about one field at once, and ship-blank is the safe reading of the
    # tie. It is also what stopped Batch Identifier: a generated per-conversion value
    # (CONV-<id>) that auto-capture kept re-stamping had been tying the seeded
    # suppression and then winning on the string tie-breaks below, which favour a
    # "default_value" over a "suppress". A STRICTLY newer value still wins — the date
    # term dominates — so this only decides a genuine same-instant tie.
    suppress_first = 0 if entry.decision == SUPPRESS else 1
    # Newest first, expressed as "how long before the end of time" so that an
    # undated entry (datetime.min) sorts last without going through a timestamp
    # — datetime.min.timestamp() is not representable in every timezone.
    return (
        datetime.max - (entry.effective_date or datetime.min),
        suppress_first,
        exact_client,
        exact_source,
        str(getattr(entry.row, "id", "") or ""),
        entry.decision,
    )


# ── The resolver ─────────────────────────────────────────────────────────────

def resolve(entries: Sequence[Entry] | Iterable[Any], *, target_field: str,
            client_id: Any = None, source_erp: str | None = None,
            sheet: str | None = None) -> Optional[Entry]:
    """The winning entry for one field, or ``None`` if nobody has said anything.

    ``entries`` may be ``Entry`` objects or raw ``LearnedMapping`` rows.

    One field has one answer. A suppression and a column mapping for the same
    field are not two different kinds of thing to be ranked against each other —
    they are two statements about the same field, and the later one is what the
    client currently wants. That is the whole rule.
    """
    pool = _as_entries(entries)
    live = [e for e in pool
            if applies(e, client_id=client_id, source_erp=source_erp,
                       target_field=target_field, sheet=sheet)]
    if not live:
        return None
    live.sort(key=lambda e: _order(e, client_id=client_id, source_erp=source_erp))
    return live[0]


def resolve_all(entries: Sequence[Entry] | Iterable[Any], *, client_id: Any = None,
                source_erp: str | None = None, sheet: str | None = None,
                target_fields: Iterable[str] | None = None) -> dict[str, Entry]:
    """Every field's winner at once, keyed by normalised field name.

    Generation asks this once per sheet rather than once per field, so a sheet
    is resolved from a single consistent read of the store.
    """
    pool = _as_entries(entries)
    wanted = ({normalise_field(f) for f in target_fields}
              if target_fields is not None else None)
    buckets: dict[str, list[Entry]] = {}
    for e in pool:
        key = normalise_field(e.target_field)
        if not key or (wanted is not None and key not in wanted):
            continue
        if not applies(e, client_id=client_id, source_erp=source_erp, sheet=sheet):
            continue
        buckets.setdefault(key, []).append(e)
    winners: dict[str, Entry] = {}
    for key, candidates in buckets.items():
        candidates.sort(key=lambda e: _order(e, client_id=client_id,
                                             source_erp=source_erp))
        winners[key] = candidates[0]
    return winners


def _as_entries(entries: Sequence[Entry] | Iterable[Any]) -> list[Entry]:
    out: list[Entry] = []
    for item in entries or ():
        if isinstance(item, Entry):
            out.append(item)
            continue
        built = entry_of(item)
        if built is not None:
            out.append(built)
    return out


# ── Reading a per-conversion row as an entry ─────────────────────────────────
#
# A `MappingSuggestion` row that a PERSON decided is a statement of exactly the
# same kind as anything in the library, so it competes on exactly the same
# terms: by date. This is what makes "the analyst mapping wins as that's the
# latest mapping as per date" true without a per-conversion scope.
#
# A row stamped `learning-engine` is NOT a statement. It is a copy of one, and
# reading it back as an entry would let the store's own output re-enter the
# store and outrank the thing it was copied from.
ENGINE = "learning-engine"


def decided_by_a_person(mapping: Any) -> bool:
    who = str(getattr(mapping, "approved_by", "") or "").strip()
    return bool(who) and who != ENGINE


def entry_from_mapping(mapping: Any, *, target_field: str, client_id: Any = None,
                       source_erp: str | None = None,
                       target_object: str | None = None) -> Optional[Entry]:
    """The dated statement a human-decided mapping row represents, or ``None``.

    ``approved_at`` is the date, because that is when the person acted — every
    deliberate edit stamps it. An edit with no stamp counts as older, per the
    rule above; it still becomes an entry so the decision is not lost, it simply
    loses to anything that can be placed in time.
    """
    if not decided_by_a_person(mapping):
        return None
    status = str(getattr(mapping, "status", "") or "")
    source_column = str(getattr(mapping, "source_column", "") or "").strip()
    default_value = getattr(mapping, "default_value", None)
    has_default = default_value is not None and str(default_value).strip() != ""
    transform = getattr(mapping, "suggested_transformation", None) or {}

    if status == "not_applicable" and not has_default:
        decision, value = SUPPRESS, None
    elif source_column:
        decision, value = SOURCE_COLUMN, source_column
    elif has_default:
        decision, value = DEFAULT_VALUE, str(default_value)
    else:
        # Nothing was actually decided — a rejected suggestion with no
        # replacement is not an instruction about what the field should be.
        return None

    return Entry(
        target_field=str(target_field),
        decision=decision,
        value=value,
        client_id=client_id,
        source_erp=source_erp,
        effective_date=getattr(mapping, "approved_at", None) or datetime.min,
        captured_from="grid",
        captured_by=str(getattr(mapping, "approved_by", "") or "") or None,
        rule_type=(transform.get("rule_type") if isinstance(transform, dict) else None),
        rule_config=(dict(transform.get("config") or {})
                     if isinstance(transform, dict) else {}),
        target_object=target_object,
        row=mapping,
    )


# ── The one writer ───────────────────────────────────────────────────────────
#
# Every path that records a mapping decision comes through here: the mapping
# workbook, the gold standard, a captured learning, the steer box, a grid edit
# and a custom rule. That is the point. A decision written any other way is a
# decision the resolver cannot see and the analyst cannot retire, and the
# six-call-sites problem — one object-scoped fan-out fixed three times in three
# different call sites because there was no single place to fix it — is exactly
# what this whole change exists to end. `tests/test_one_dated_store_writes.py`
# asserts that no other path writes one.

# How each decision is laid out on the stored row. The column names are the old
# vocabulary and stay as they are, because the Learning Centre, every seeder and
# the analyst's own JSON all speak it.
_DEFAULT_CATEGORY = {
    SOURCE_COLUMN: "Column Mapping Alias",
    DEFAULT_VALUE: "Default Value",
    SUPPRESS: "Left blank on purpose",
    RULE: "Transformation Rule",
}


def _row_shape(decision: str, value: Optional[str], target_field: str,
               rule_type: Optional[str], rule_config: dict) -> dict:
    """``(original_value, resolved_value, rule_type, rule_config)`` for a decision."""
    if decision == DEFAULT_VALUE:
        cfg = dict(rule_config or {})
        cfg.setdefault("default_value", "" if value is None else str(value))
        return {"original_value": "(default)",
                "resolved_value": "" if value is None else str(value),
                "rule_type": rule_type or "default", "rule_config": cfg}
    if decision == SUPPRESS:
        return {"original_value": "(blank)", "resolved_value": "",
                "rule_type": rule_type or "suppress",
                "rule_config": dict(rule_config or {})}
    # source_column and rule both name a source column and resolve to the field.
    return {"original_value": "" if value is None else str(value),
            "resolved_value": str(target_field),
            "rule_type": rule_type, "rule_config": dict(rule_config or {})}


def sheet_key(sheets: Iterable[Any] | None) -> frozenset:
    """THE FOURTH KEY DIMENSION: which interface this decision is about.

    Analyst, 05-Aug: "client, source, field, sheet should be 4 dimensions for all
    columns, for all source and fields." Insert Update Indicator is why — it must
    be "I" on one interface and blank on another, and until now it could not,
    because the store keyed (client, source, field) and one field held ONE value.
    A per-sheet keep-blank and a field-wide default collapsed into a single row.

    A decision's sheet identity is the normalised set of sheets it names. The
    empty set is FIELD-WIDE — every decision written before this, and any decision
    the analyst makes "for all sheets", carries it. So an empty-set write behaves
    exactly as it did before: one row per field. The dimension only splits rows
    once a writer names a sheet, which keeps this change inert until it is used.

    exclude_sheets is deliberately not part of the identity. The per-sheet UI
    writes an inclusion (``sheets=[X]``); "everywhere except X" remains one
    field-wide statement that the resolver narrows at read time.
    """
    return frozenset(
        normalise_field(s) for s in (sheets or []) if str(s).strip())


def _row_sheet_key(row: Any) -> frozenset:
    return sheet_key(getattr(row, "sheets", None))


def _merge_sheets(previous: Iterable[Any], incoming: Iterable[Any]) -> list:
    """Union within one sheet identity.

    Rows are now partitioned by ``sheet_key`` before they reach here, so previous
    and incoming already share an identity — this only de-dups spellings and keeps
    the list stable. Across identities there is nothing to merge: a ``sheets=["A"]``
    statement and a ``sheets=["B"]`` statement are two different rows now, one per
    interface, which is the whole point of the fourth dimension.
    """
    seen, merged = set(), []
    for item in [*(previous or []), *(incoming or [])]:
        key = str(item).strip().lower()
        if item and key not in seen:
            seen.add(key)
            merged.append(item)
    return merged


async def find_rows_for_key(*, target_field: str, client_id: Any = None,
                            source_erp: str | None = None,
                            include_deleted: bool = True) -> list:
    """Every stored decision for ``(client, source, field)``, whatever the object.

    The object is deliberately not in the query. A decision captured under
    "Supplier" and one captured under "Supplier Import" are the same client
    saying two things about one field, and the later one is what they want. It
    was possible for those two spellings to hide from each other, and that is a
    documented way for a decision to disappear.
    """
    from app.models.learned import LearnedMapping
    rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(DECISION_KINDS)}},
        {"target_field": {"$ne": None}},
        include_deleted=include_deleted,
    ).to_list()
    want_field = normalise_field(target_field)
    want_client = client_key(client_id)
    want_source = normalise_source(source_erp)
    out = []
    for row in rows:
        if normalise_field(getattr(row, "target_field", None)) != want_field:
            continue
        if client_key(getattr(row, "client_id", None)) != want_client:
            continue
        if normalise_source(getattr(row, "source_erp", None)) != want_source:
            continue
        out.append(row)
    return out


# What a write should do about the rows already stored. Pure, so that "an older
# statement never overwrites a newer one" — the property every replay, reseed and
# backfill depends on — can be tested against a table instead of a database.
SKIP_RETIRED = "skip-retired"
SKIP_OLDER = "skip-older"
UPDATE = "update"
INSERT = "insert"


REFRESH = "refresh"   # update the content, leave the stored date exactly where it is


def plan_write(existing: Sequence[Any], *, kind: str, when: Optional[datetime],
               revive: bool = False, captured_from: str | None = None
               ) -> tuple[str, Any]:
    """``(what to do, which row)`` for one incoming statement.

    ``when=None`` means the statement carries no date — a bundled data file that
    never said when it was written. Such a statement cannot win a contest, so it
    may only create a row or **refresh one it wrote itself** (same
    ``captured_from``). That is what stops a startup seed from walking the
    analyst's own correction backwards, and it is why it does not simply stamp
    itself with today: a seed re-running on every boot would then out-rank every
    instruction ever given, which is exactly what ``captured_at`` used to do.
    """
    # ONE ROW PER KEY, WHATEVER THE KIND. (05-Aug)
    #
    # This partitioned by ``kind``: a column_mapping, an example_default and a
    # rule for the same field were three separate rows, each with its own date,
    # all alive at once. That is a fallback — and a fallback is something
    # generation can reach for when the newest statement does not suit it.
    #
    # Receipt Routing is what it cost. A rule dated 03-Aug and a fixed value dated
    # 04-Aug both existed; generation consulted them in code order rather than
    # date order, and the file shipped a third value from a 13-Jul document.
    # Three statements about one field, none of them wrong on its own.
    #
    # Analyst, 05-Aug: "there should be just one row for each mapping or fixed
    # value stored with date ... so that it does not even have other previous
    # mappings or values to refer to even if it wants."
    #
    # So the four DECISION_KINDS now compete for ONE row. A fixed value typed
    # today does not outrank yesterday's rule — it REPLACES it, and
    # record_decision deletes anything else still sitting under the key.
    #
    # Deliberately NOT true of the other kinds on LearnedMapping: ``crosswalk``
    # is one row per source VALUE and must stay many-per-field, and
    # ``file_signature``, ``ignore_source`` and ``reference_standard`` are not
    # statements about how a field maps. find_rows_for_key already queries only
    # the four, which is what makes collapsing them safe.
    decisions = [r for r in existing if getattr(r, "kind", None) in DECISION_KINDS]
    if not decisions:
        return (INSERT, None)
    # Newest first, so an edit lands on the row the resolver would have picked
    # rather than on whichever happened to be inserted first.
    decisions = sorted(decisions, key=effective_of, reverse=True)
    row = decisions[0]
    if getattr(row, "is_deleted", False) and not revive:
        # The analyst retired this. An automatic path — a seed, an auto-capture,
        # a replay — must not bring it back.
        return (SKIP_RETIRED, row)
    if when is None:
        if captured_from and getattr(row, "captured_from", None) == captured_from:
            # The same file restating itself. Not a competing statement.
            return (REFRESH, row)
        return (SKIP_OLDER, row)
    if when < effective_of(row):
        # Someone has already said something later about this field. Saying an
        # older thing again does not change what they want.
        return (SKIP_OLDER, row)
    return (UPDATE, row)


async def record_decision(*, decision: str, target_field: str,
                          value: Optional[str] = None, client_id: Any = None,
                          source_erp: str | None = None,
                          effective_date: Optional[datetime] = None,
                          captured_from: str, captured_by: str | None = None,
                          rule_type: str | None = None,
                          rule_config: dict | None = None,
                          target_object: str | None = None,
                          project_id: Any = None,
                          undated: bool = False,
                          sheets: Sequence[str] | None = None,
                          exclude_sheets: Sequence[str] | None = None,
                          category: str | None = None,
                          is_global: bool = False,
                          original_value: str | None = None,
                          resolved_value: str | None = None,
                          revive: bool = False):
    """Record one dated statement about one field. The only way to write one.

    ``effective_date`` is when the INSTRUCTION was given. Pass it when you know
    it — a seeded file carries the date of the file, a grid edit carries the
    moment the analyst pressed the button. Omitted, it is now, which is right for
    something typed into the UI and wrong for anything replayed, so replays must
    pass it.

    **An older statement never overwrites a newer one.** A re-run of a seed, a
    backfill, a redeploy: if what is already stored was said later, this returns
    it untouched and nothing moves. That is what makes every writer here safe to
    run twice, and it is why a startup seed that finds nothing to do cannot
    re-stamp the library — which is precisely what ``captured_at`` did, inverting
    precedence on every redeploy.

    Returns the stored row, or ``None`` when the analyst has retired this
    decision and ``revive`` was not set.
    """
    from app.models.learned import LearnedMapping

    if decision not in DECISION_TO_KIND:
        raise ValueError(f"not a mapping decision: {decision!r}")
    if not target_field or not str(target_field).strip():
        raise ValueError("a decision must be about a target field")

    kind = DECISION_TO_KIND[decision]
    now = datetime.utcnow()
    # `undated` is for bundled data files that never said when they were written.
    # They carry no date rather than today's: a seed re-running on every boot and
    # stamping itself with `now` would out-rank every instruction ever given.
    when = None if (undated and effective_date is None) else (effective_date or now)
    shape = _row_shape(decision, value, target_field, rule_type, rule_config or {})
    if original_value is not None:
        shape["original_value"] = original_value
    if resolved_value is not None:
        shape["resolved_value"] = resolved_value

    # The identity of a stored row is (client, source, field, SHEET) — the key.
    # One field on one interface holds at most one live decision, and those compete
    # by date. A decision about a DIFFERENT interface is a different row, which is
    # what lets Insert Update Indicator be "I" on one sheet and blank on another.
    # The object is NOT part of the identity; the sheet is (05-Aug).
    _want_sheets = sheet_key(sheets)
    rows = await find_rows_for_key(target_field=target_field, client_id=client_id,
                                   source_erp=source_erp, include_deleted=True)
    # Only rows about the SAME interface(s) compete for this write. A field-wide
    # row (empty set) and a sheet-B row are left untouched by a sheet-A write.
    rows = [r for r in rows if _row_sheet_key(r) == _want_sheets]
    action, row = plan_write(rows, kind=kind, when=when, revive=revive,
                             captured_from=captured_from)

    if action == SKIP_RETIRED:
        return None
    if action == SKIP_OLDER:
        return row
    if action in (UPDATE, REFRESH):
        patch = {
            **shape,
            # THE KIND MOVES WITH THE STATEMENT.
            #
            # One row per key means the row's kind is whatever the newest
            # statement is: a field that carried a rule yesterday and a fixed
            # value today is a default_value row now, not two rows. Leaving the
            # old kind in place would have kept the row resolving as a rule while
            # holding a constant's value — a fallback hiding inside one document.
            "kind": kind,
            "category": category or _DEFAULT_CATEGORY[decision],
            "captured_from": captured_from,
            "captured_by": captured_by,
            "captured_at": now,
            "client_id": client_id,
            "source_erp": source_erp,
            "project_id": project_id,
            "is_global": is_global,
            # Provenance: where this was most recently said, not where it applies.
            "target_object": target_object,
        }
        if action == UPDATE:
            # REFRESH deliberately leaves the stored date alone — the file is
            # restating itself, not saying something new.
            patch["effective_date"] = when
        if sheets is not None:
            patch["sheets"] = _merge_sheets(getattr(row, "sheets", None), sheets)
        if exclude_sheets is not None:
            patch["exclude_sheets"] = _merge_sheets(
                getattr(row, "exclude_sheets", None), exclude_sheets)
        if revive and getattr(row, "is_deleted", False):
            patch.update({"is_deleted": False, "deleted_at": None,
                          "deleted_by": None})
        # ARCHIVE THE STATEMENT THIS REPLACES, BEFORE IT IS OVERWRITTEN.
        #
        # One row per key means a newer statement lands ON the existing row, so
        # the older one does not get deleted — it gets mutated out of existence.
        # Archiving only the rows _delete_other_decisions removes would therefore
        # have caught almost nothing: in the ordinary two-statement case there IS
        # no second row, and the previous decision would vanish with no trace at
        # all. That is worse than the hard delete it was meant to soften.
        #
        # REFRESH is excluded on purpose: that is the same dated file restating
        # itself, not a new statement, so there is nothing being superseded.
        if action == UPDATE:
            try:
                await _archive(row, superseded_by=getattr(row, "id", None),
                               reason="superseded")
            except Exception:  # noqa: BLE001 — never fail a write over the trail
                log.exception("could not archive the previous decision for %r",
                              target_field)
        await row.set(patch)
        await _delete_other_decisions(row, target_field=target_field,
                                      client_id=client_id, source_erp=source_erp)
        return row

    row = LearnedMapping(
        kind=kind,
        category=category or _DEFAULT_CATEGORY[decision],
        target_field=str(target_field),
        target_object=target_object,
        client_id=client_id,
        source_erp=source_erp,
        project_id=project_id,
        is_global=is_global,
        captured_from=captured_from,
        captured_by=captured_by,
        effective_date=when,
        sheets=list(sheets or []),
        exclude_sheets=list(exclude_sheets or []),
        **shape,
    )
    await row.insert()
    await _delete_other_decisions(row, target_field=target_field,
                                  client_id=client_id, source_erp=source_erp)
    return row


async def _delete_other_decisions(keep, *, target_field: str, client_id: Any,
                                  source_erp: str | None) -> int:
    """Remove every OTHER decision row under this key. Returns how many went.

    "There should be just one row for each mapping or fixed value stored with
    date ... so that it does not even have other previous mappings or values to
    refer to even if it wants" — analyst, 05-Aug. The point is not tidiness. A
    row that still exists is a row some future code path can read, and every
    screen-versus-file bug in this tool has been two stored statements
    disagreeing. Deleting the losers is what makes the winner unambiguous
    structurally rather than by everybody remembering to sort by date.

    ARCHIVED, NOT DELETED — analyst, 05-Aug: "keep the older rules in archival
    currently, do not hard delete it but do not fall back to it, we will delete
    it after testing." The row is copied into ``ArchivedMappingDecision`` and
    removed from ``LearnedMapping``, so the guarantee is unchanged — the resolver
    queries LearnedMapping and finds exactly one row — while the trail survives
    until the analyst is satisfied.

    A separate COLLECTION rather than a flag, deliberately. A flag would leave
    the superseded statement sitting in the collection every reader already
    queries, which is the fallback this whole change exists to remove. Switching
    to a hard delete later is one branch here, not a rewrite.

    Only the four DECISION_KINDS are touched. Crosswalks are one row per source
    VALUE and must stay many-per-field; file signatures, ignore_source and
    reference standards are not statements about how a field maps.

    ONLY THE SAME INTERFACE. With sheet in the key, "one row per key" means one row
    per (client, source, field, sheet). A decision about sheet A must not delete
    the field-wide row or a sheet-B decision — those are different keys, and wiping
    them is precisely the collapse that made per-sheet values impossible. So the
    losers are filtered to the survivor's own sheet identity.
    """
    keep_sheets = _row_sheet_key(keep)
    rows = await find_rows_for_key(target_field=target_field, client_id=client_id,
                                   source_erp=source_erp, include_deleted=True)
    gone = 0
    for other in rows:
        if getattr(other, "id", None) == getattr(keep, "id", None):
            continue
        if getattr(other, "kind", None) not in DECISION_KINDS:
            continue
        if _row_sheet_key(other) != keep_sheets:
            # A different interface (or the field-wide base). Not this key.
            continue
        try:
            await _archive(other, superseded_by=getattr(keep, "id", None),
                           reason="superseded")
            await other.delete()
            gone += 1
        except Exception:  # noqa: BLE001 — a failed cleanup must not fail the write
            log.exception("could not archive superseded decision %s for %r",
                          getattr(other, "id", None), target_field)
    if gone:
        log.info("one-row store: archived %d superseded decision(s) for %r",
                 gone, target_field)
    return gone


async def _archive(row, *, superseded_by=None, reason: str = "superseded") -> None:
    """Copy a decision into the archive collection before it leaves the store.

    Raises on failure, so the caller does NOT delete a row it could not archive —
    losing a decision silently is the one outcome worse than keeping a duplicate.
    """
    from app.models.learned import ArchivedMappingDecision

    await ArchivedMappingDecision(
        kind=getattr(row, "kind", "") or "",
        category=getattr(row, "category", None),
        original_value=getattr(row, "original_value", None),
        resolved_value=getattr(row, "resolved_value", None),
        target_object=getattr(row, "target_object", None),
        target_field=getattr(row, "target_field", None),
        rule_type=getattr(row, "rule_type", None),
        rule_config=getattr(row, "rule_config", None),
        client_id=getattr(row, "client_id", None),
        is_global=bool(getattr(row, "is_global", False)),
        project_id=getattr(row, "project_id", None),
        captured_from=getattr(row, "captured_from", None),
        captured_by=getattr(row, "captured_by", None),
        captured_at=getattr(row, "captured_at", None),
        effective_date=getattr(row, "effective_date", None),
        source_erp=getattr(row, "source_erp", None),
        sheets=list(getattr(row, "sheets", None) or []),
        exclude_sheets=list(getattr(row, "exclude_sheets", None) or []),
        is_deleted=bool(getattr(row, "is_deleted", False)),
        original_id=getattr(row, "id", None),
        superseded_by=superseded_by,
        reason=reason,
    ).insert()


async def collapse_existing_decisions(dry_run: bool = False) -> dict:
    """Bring data written BEFORE the one-row rule into line. Idempotent.

    ``plan_write`` now keeps a single decision row per (client, source, field),
    but that only applies from the next write onwards. Everything stored under
    the old shape still carries up to four live rows per field — one per kind —
    and those are exactly the fallbacks this change exists to remove. A key
    nobody happens to edit again would keep them forever.

    So: for every key, keep the newest decision and delete the rest. Same rule as
    the writer, applied to history once.

    A RETIRED row is kept if it is the newest, because the tombstone is itself
    the analyst's latest statement about that field and removing it would revive
    what they retired.

    Losers are ARCHIVED, not deleted — see ``_archive``. ``dry_run=True`` reports
    what would move without touching anything.
    """
    from app.models.learned import LearnedMapping

    rows = await LearnedMapping.find(
        {"kind": {"$in": sorted(DECISION_KINDS)}},
        {"target_field": {"$ne": None}},
        include_deleted=True,
    ).to_list()

    by_key: dict[tuple, list] = {}
    for row in rows:
        # Sheet is part of the key now (05-Aug). Collapsing across sheets would
        # delete a per-interface decision as a "duplicate" of a field-wide one and
        # undo exactly the per-sheet control this dimension exists to give.
        key = (client_key(getattr(row, "client_id", None)),
               normalise_source(getattr(row, "source_erp", None)),
               normalise_field(getattr(row, "target_field", None)),
               _row_sheet_key(row))
        by_key.setdefault(key, []).append(row)

    kept = removed = 0
    collapsed_keys = 0
    for key, group in by_key.items():
        kept += 1
        if len(group) < 2:
            continue
        collapsed_keys += 1
        # Newest first — the same ordering the resolver uses, so the survivor is
        # the row that was already winning. This must not change any answer; it
        # removes the losers, it does not promote one.
        group = sorted(group, key=effective_of, reverse=True)
        for loser in group[1:]:
            removed += 1
            if not dry_run:
                try:
                    await _archive(loser, superseded_by=getattr(group[0], "id", None),
                                   reason="collapsed")
                    await loser.delete()
                except Exception:  # noqa: BLE001
                    log.exception("could not archive/collapse decision %s",
                                  getattr(loser, "id", None))
    log.info("one-row store: %d key(s), %d had duplicates, %d row(s) %s",
             kept, collapsed_keys, removed,
             "would be archived" if dry_run else "archived")
    return {"keys": kept, "keys_with_duplicates": collapsed_keys,
            "rows_removed": removed, "dry_run": dry_run}


async def record_reference_standard(*, target_object: str | None,
                                    target_field: str, original_value: str = "",
                                    resolved_value: str = "",
                                    rule_type: str | None = None,
                                    rule_config: dict | None = None,
                                    captured_from: str, captured_by: str | None = None,
                                    client_id: Any = None,
                                    source_erp: str | None = None,
                                    project_id: Any = None,
                                    effective_date: Optional[datetime] = None,
                                    category: str = "Reference Key Standard",
                                    revive: bool = False):
    """A master-key format standard.

    Not a statement about how a field MAPS — it records the shape a reference key
    takes — so it is not one of the four decisions and the resolver does not rank
    it against them. It lives here anyway so that the store stays the only place
    that writes a learning, and so it gets the same guarantee: an older statement
    never overwrites a newer one.
    """
    from app.models.learned import LearnedMapping

    now = datetime.utcnow()
    when = effective_date or now
    # Reference standards ARE scoped by object: "the Item number format" and "the
    # Supplier number format" are different facts about different keys, not two
    # statements about one field.
    rows = [r for r in await _reference_rows(target_field, client_id, source_erp,
                                            include_deleted=True)
            if _same_object(r, target_object)]
    action, row = plan_write(rows, kind="reference_standard", when=when,
                             revive=revive)
    if action == SKIP_RETIRED:
        return None
    if action == SKIP_OLDER:
        return row
    patch = {
        "original_value": original_value, "resolved_value": resolved_value,
        "rule_type": rule_type, "rule_config": dict(rule_config or {}),
        "captured_from": captured_from, "captured_by": captured_by,
        "captured_at": now, "effective_date": when, "client_id": client_id,
        "source_erp": source_erp, "project_id": project_id,
        "target_object": target_object, "category": category,
    }
    if action == UPDATE:
        if revive and getattr(row, "is_deleted", False):
            patch.update({"is_deleted": False, "deleted_at": None,
                          "deleted_by": None})
        await row.set(patch)
        return row
    row = LearnedMapping(kind="reference_standard", target_field=str(target_field),
                         is_global=False, sheets=[], exclude_sheets=[], **patch)
    await row.insert()
    return row


async def _reference_rows(target_field: str, client_id: Any,
                          source_erp: str | None,
                          include_deleted: bool = True) -> list:
    from app.models.learned import LearnedMapping
    rows = await LearnedMapping.find({"kind": "reference_standard"},
                                     include_deleted=include_deleted).to_list()
    want_field = normalise_field(target_field)
    return [r for r in rows
            if normalise_field(getattr(r, "target_field", None)) == want_field
            and client_key(getattr(r, "client_id", None)) == client_key(client_id)
            and normalise_source(getattr(r, "source_erp", None))
            == normalise_source(source_erp)]


def _same_object(row: Any, target_object: str | None) -> bool:
    return normalise_field(getattr(row, "target_object", None)) == \
        normalise_field(target_object)


async def record_learning(*, kind, category, original_value, resolved_value,
                        target_object=None, target_field=None, rule_type=None,
                        rule_config=None, project_id=None, captured_from, captured_by,
                        client_id=None, source_erp=None, sheets=None,
                        exclude_sheets=None, effective_date=None,
                        undated: bool = False, revive: bool = False):
    """Record a decision. A thin adapter onto the one dated store.

    Everything real happens in ``record_decision``: the key is
    ``(client, source system, target field)``, the newest statement wins, and an
    older one never overwrites a newer one. This function exists only so the
    long-standing callers keep their argument names.

    ``revive=False`` (the default) honours the tombstone: if the analyst deleted
    this decision, an automatic path — auto-capture after Generate, a startup
    seed, an approve/override — must NOT bring it back. Only an explicit user
    action passes ``revive=True``. Returns ``None`` when a retired row was left
    untouched.
    """
    if kind in DECISION_KINDS:
        return await record_decision(
            decision=KIND_TO_DECISION[kind],
            target_field=target_field,
            value=value_for(kind, original_value=original_value,
                                          resolved_value=resolved_value,
                                          rule_config=rule_config),
            client_id=client_id, source_erp=source_erp,
            effective_date=effective_date, undated=undated,
            captured_from=captured_from, captured_by=captured_by,
            rule_type=rule_type, rule_config=rule_config,
            target_object=target_object, project_id=project_id,
            sheets=sheets, exclude_sheets=exclude_sheets, category=category,
            original_value=original_value, resolved_value=resolved_value,
            revive=revive,
        )
    if kind == "reference_standard":
        return await record_reference_standard(
            target_object=target_object, target_field=target_field,
            original_value=original_value or "", resolved_value=resolved_value or "",
            rule_type=rule_type, rule_config=rule_config,
            captured_from=captured_from, captured_by=captured_by,
            client_id=client_id, source_erp=source_erp, project_id=project_id,
            effective_date=effective_date, category=category, revive=revive,
        )
    raise ValueError(f"not a learning this store records: {kind!r}")


async def record_source_exclusion(*, target_object: str | None, source_column: str,
                                  captured_from: str, captured_by: str | None = None,
                                  client_id: Any = None,
                                  source_erp: str | None = None,
                                  effective_date: Optional[datetime] = None,
                                  category: str = "Do Not Map Source",
                                  revive: bool = False):
    """"Never map anything FROM this source column."

    Not one of the four decisions — it is about a source column, not a target
    field, so the resolver does not rank it against them. It lives here so the
    store stays the only place a learning is written.
    """
    from app.models.learned import LearnedMapping

    now = datetime.utcnow()
    rows = await LearnedMapping.find({"kind": "ignore_source"},
                                     include_deleted=True).to_list()
    want = normalise_field(source_column)
    rows = [r for r in rows
            if normalise_field(getattr(r, "original_value", None)) == want
            and _same_object(r, target_object)]
    if rows:
        row = rows[0]
        if getattr(row, "is_deleted", False) and not revive:
            return None
        await row.set({"client_id": client_id, "source_erp": source_erp,
                       "captured_from": captured_from, "captured_by": captured_by,
                       "captured_at": now})
        return row
    row = LearnedMapping(
        kind="ignore_source", category=category,
        original_value=source_column, resolved_value="",
        target_object=target_object, target_field=None,
        client_id=client_id, is_global=False, source_erp=source_erp,
        captured_from=captured_from, captured_by=captured_by,
        effective_date=effective_date,
    )
    await row.insert()
    return row
