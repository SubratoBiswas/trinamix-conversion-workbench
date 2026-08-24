"""The dated mapping store — the pure read/resolve core (Phase 3, slice 4).

Relocated verbatim from ``app.services.mapping_store`` so the whole of the analyst's
"one key, one date, one winner" rule — the decision vocabulary, the ``Entry``, how a
stored row or a per-conversion mapping becomes an ``Entry``, which entries apply, and
which one wins — lives in the domain, where the invariant is *no I/O*. The store module
keeps only the Mongo-facing adapter (querying rows, writing decisions, compaction) and
imports this core, so every public name is still reachable as ``mapping_store.X`` and
behaviour is byte-identical.

    "Mappings, learnings and user inputs should be stored in the same place with date
     (with respect to client and source), whichever is latest." — analyst, 02-Aug-2026

Every statement about how a field should map is a dated ``Entry`` keyed
``(client_id, source_erp, target_field)``. The newest one wins; who wrote it is
provenance, never precedence. Pure — it takes entries and answers questions about
them — so it is testable against a table of competing entries without a database.
"""
from __future__ import annotations

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
