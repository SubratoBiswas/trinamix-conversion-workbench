"""Static analysis of a rule's config: which SOURCE columns does it read? (Phase 2, slice 2)

Relocated VERBATIM out of app.services.output_service. The generator prunes the source
frame to the columns something claims and builds each row's context from that same set,
so a column a rule reads but does not declare is pruned away and the rule silently sees a
blank — the class of defect behind "Supplier Site shipped empty on 8,561 rows" and the
Taxpayer-ID CASE_WHEN shipping the literal ``{tax_id}``. These functions enumerate every
such column so the frame keeps it.

Pure: ``re`` + recursion only, no pandas, no I/O, no service imports. The counterpart to
the interpolation/branch helpers in ``app.domain.rules.context`` (which do the runtime
substitution these predict). output_service and the strategy overlay import these back
under their historical underscore names, so call sites are unchanged."""
from __future__ import annotations

import re


def flat_cols(spec) -> list:
    """A rule column may be one name or a LIST of candidate spellings. Both have
    to survive source pruning: a list that reaches the frame as a single unhashable
    entry declares nothing, and the rule then reads blanks off a pruned frame."""
    if spec is None:
        return []
    if isinstance(spec, (list, tuple)):
        out = []
        for c in spec:
            out.extend(flat_cols(c))
        return out
    return [spec] if str(spec).strip() else []


def branch_columns(branches) -> set[str]:
    """Every column a branch list reads, including nested ``all`` / ``any``
    conjunctions. A conjunction's columns are named nowhere else, so leaving them
    undeclared prunes them out and every clause reads blank — which for Party
    Type means the organization-name test always passes and every row is PERSON."""
    cols: set[str] = set()
    for br in branches or []:
        if not isinstance(br, dict):
            continue
        for key in ("all", "any"):
            if isinstance(br.get(key), (list, tuple)):
                cols |= branch_columns(br[key])
        cols.update(flat_cols(br.get("if_column")))
    return cols


# ``{Column}`` inside a rule's RESULT string — the same token the engine's
# ``_interpolate`` substitutes from the row. Kept identical to
# ``transformations.engine._PLACEHOLDER`` so the two cannot disagree about what a
# token is.
_RESULT_TOKEN = re.compile(r"\{([^{}]+)\}")


def interpolated_columns(*values) -> set[str]:
    """Columns a rule reads through ``{Column}`` interpolation in its RESULT — a
    CASE_WHEN branch's ``then`` (or the top-level ``default``) and a CONDITIONAL's
    ``then`` / ``else``.

    These are named nowhere else on the rule, so a walk that only looks at
    ``if_column`` misses them entirely — the frame prunes the column and the engine
    ships the LITERAL token to the file. Reported 06-Aug (NextPower Supplier): a
    Taxpayer-ID CASE_WHEN mapped India->``{pan}``, United States->``{tax_id}``,
    Canada->``{tax_id_canada}`` shipped the raw text ``{tax_id}`` / ``{tax_id_canada}``
    for the US and Canada rows. ``pan`` resolved only because it was the rule's own
    ``source_column`` and so survived pruning; the other two were referenced solely
    inside a ``then`` and were dropped before the rule ran."""
    cols: set[str] = set()
    for v in values:
        if isinstance(v, str) and "{" in v:
            for m in _RESULT_TOKEN.finditer(v):
                name = m.group(1).strip()
                if name:
                    cols.add(name)
    return cols


def rule_referenced_columns(rules: list[dict]) -> set[str]:
    """Source columns a rule reads OTHER than the cell's own mapped column —
    CASE_WHEN/CONDITIONAL ``if_column`` and CONCAT/COALESCE ``columns``. These must
    survive source-column pruning and be present in the per-row context, otherwise a
    derivation from a non-mapped column (e.g. Delivery Method from Email/Fax
    Transaction flags) silently sees blanks."""
    cols: set[str] = set()
    for r in rules or []:
        cfg = r.get("config") or {}
        rt = (r.get("rule_type") or "").upper()
        # The rule's OWN source column. Undeclared, it is pruned out of the frame
        # before the rule ever runs — the exact failure that made Supplier Site ship
        # empty on 8,561 rows, one layer over.
        if r.get("source_column"):
            cols.add(r["source_column"])
        # A chained rule (config["then"]) reads its own columns and is invisible
        # to a walk that only looks at the top level.
        for _nxt in (cfg.get("then") or []):
            if isinstance(_nxt, dict) and _nxt.get("rule_type"):
                cols |= rule_referenced_columns([_nxt])
        if rt in ("CONCAT", "COALESCE"):
            cols.update(flat_cols(cfg.get("columns")))
            # Literal-segment CONCAT: the column pieces live under `parts` as
            # {"col": name} entries — declare them too, or a parts-based CONCAT has
            # its own columns pruned out of the frame and reads blank.
            for _seg in (cfg.get("parts") or []):
                if isinstance(_seg, dict) and "literal" not in _seg and _seg.get("col"):
                    cols.update(flat_cols(_seg.get("col")))
                elif isinstance(_seg, str):
                    cols.update(flat_cols(_seg))
        elif rt == "CONDITIONAL":
            cols.update(flat_cols(cfg.get("if_column")))
            # ``then`` / ``else`` may build the result from other columns.
            cols |= interpolated_columns(cfg.get("then"), cfg.get("else"))
        elif rt in ("CASE_WHEN", "SUFFIX_WHEN"):
            cols |= branch_columns(cfg.get("branches"))
            if rt == "CASE_WHEN":
                # A branch's ``then`` (and the top-level ``default``) can name other
                # columns via ``{Column}`` interpolation — the Taxpayer-ID rule maps
                # each country to a DIFFERENT source column that way. Collect them or
                # the frame prunes them and the literal ``{tax_id}`` token ships.
                for _br in (cfg.get("branches") or []):
                    if isinstance(_br, dict):
                        cols |= interpolated_columns(_br.get("then"))
                cols |= interpolated_columns(cfg.get("default"))
        elif rt == "CITY_COUNTRY_KEY":
            for spec in (cfg.get("country_column"), cfg.get("city_column")):
                for c in (spec if isinstance(spec, (list, tuple)) else [spec]):
                    if c:
                        cols.add(c)
        elif rt == "SELF_LOOKUP":
            # Parent Supplier reads THREE source columns and owns none of them, so
            # every one has to survive pruning: the key it looks up by, the column
            # it matches against, and the column whose value it returns.
            cols.update(c for c in (cfg.get("key_column"), cfg.get("match_column"),
                                    cfg.get("value_column")) if c)
        elif rt == "SEQUENCE":
            # The variant condition can name a source column as easily as a target
            # one (Party Number's names a target). Declaring it costs nothing when
            # it is a target — the frame simply has no such column — and is the
            # difference between working and silently defaulting when it is not.
            _v = cfg.get("variant") or {}
            cols |= branch_columns([_v]) if _v else set()
            # "unique sequence on the basis of entityid" — the key column is a
            # SOURCE column the field does not own, so it is pruned unless declared.
            cols.update(flat_cols(cfg.get("key_column")))
    return cols
