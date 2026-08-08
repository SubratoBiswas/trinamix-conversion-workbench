"""Generate the Fusion-ready FBDI output (async/Beanie)."""
from __future__ import annotations

import asyncio
import contextlib
import time as _time
import io
import logging
import json
import re
import zipfile
from pathlib import Path
from datetime import datetime
from typing import Any

import pandas as pd

from app.config import settings
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.output import ConvertedOutput
from app.models.conversion import Conversion
from app.models.transformation import TransformationRule
from app.parsers import parse_tabular
from app.services.learning_service import REFERENCE_KEY_FIELDS
from app.transformations import apply_pipeline
log = logging.getLogger(__name__)

from app.services.strategy_overlay import (
    directive_for as _strategy_directive,
    blank_fields as _strategy_blank_fields,
    apply_frame_rules as _strategy_frame_rules,
)

# Which mapping status wins when several rows target the same field. An analyst
# override beats an approval, an approval beats a deliberate "not applicable", and
# "suggested" (auto-map guessing) always loses. Module level so generation and the
# required-field gate agree on the winning row — two copies would drift.
@contextlib.contextmanager
def _phase(name: str, obj: str = ""):
    """Log how long one phase of a generate took.

    Coarse on purpose. The question "can this be quicker" needs to be answerable
    from a log line after a real run, not from someone reading the code and
    guessing at the hot loop — which is how the last two attempts went.
    """
    _t = _time.monotonic()
    try:
        yield
    finally:
        log.info("generate phase — %s%s took %.1fs", f"{obj}: " if obj else "",
                 name, _time.monotonic() - _t)


# Which mapping row wins is ONE rule, shared with the screen — see
# services/mapping_dedupe. This module used to carry three separate copies of it
# that compared status alone, so a tie was broken by whichever row Mongo returned
# first and the generated file could disagree with Mapping Review.
from app.services.mapping_dedupe import best_mapping_by_target  # noqa: E402


async def _get_reference_standards(target_object: str | None) -> dict:
    from app.models.learned import LearnedMapping
    if not target_object:
        return {}
    standards = await LearnedMapping.find({"kind": "reference_standard", "target_object": target_object}).to_list()
    return {s.target_field: {"rule_type": s.rule_type, "config": s.rule_config or {}} for s in standards if s.rule_type}


# Rows per chunk for the streaming transform. The transform is row-local, so we
# process the source in windows and concat — this bounds peak memory (the per-row
# ``records`` context + column caches stay chunk-sized instead of whole-file) and
# keeps the CPU work in slices we can hand to a worker thread.
_TRANSFORM_CHUNK_ROWS = 25_000


_ATTR_RE = re.compile(r"^(global[_ ]?)?attribute([_ ]?(category|date|number|timestamp|char))?[_ ]?\d*$")


def _is_attribute_column(name: str | None) -> bool:
    """True for any Oracle descriptive-flexfield (DFF) attribute column.

    Covers ATTRIBUTE1..30, ATTRIBUTE_CATEGORY, ATTRIBUTE_DATE/NUMBER/TIMESTAMP/CHAR n
    and the GLOBAL_ATTRIBUTE* variants, in any spacing/casing the templates use.
    NextPower does not use DFFs; populating them risks failing DFF validation on load.
    """
    n = re.sub(r"[^a-z0-9_ ]", "", str(name or "").strip().lower())
    return bool(_ATTR_RE.match(n.replace(" ", "_")))


def _flat_cols(spec) -> list:
    """A rule column may be one name or a LIST of candidate spellings. Both have
    to survive source pruning: a list that reaches the frame as a single unhashable
    entry declares nothing, and the rule then reads blanks off a pruned frame."""
    if spec is None:
        return []
    if isinstance(spec, (list, tuple)):
        out = []
        for c in spec:
            out.extend(_flat_cols(c))
        return out
    return [spec] if str(spec).strip() else []


def _branch_columns(branches) -> set[str]:
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
                cols |= _branch_columns(br[key])
        cols.update(_flat_cols(br.get("if_column")))
    return cols


# ``{Column}`` inside a rule's RESULT string — the same token the engine's
# ``_interpolate`` substitutes from the row. Kept identical to
# ``transformations.engine._PLACEHOLDER`` so the two cannot disagree about what a
# token is.
_RESULT_TOKEN = re.compile(r"\{([^{}]+)\}")


def _interpolated_columns(*values) -> set[str]:
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


def _rule_referenced_columns(rules: list[dict]) -> set[str]:
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
                cols |= _rule_referenced_columns([_nxt])
        if rt in ("CONCAT", "COALESCE"):
            cols.update(_flat_cols(cfg.get("columns")))
            # Literal-segment CONCAT: the column pieces live under `parts` as
            # {"col": name} entries — declare them too, or a parts-based CONCAT has
            # its own columns pruned out of the frame and reads blank.
            for _seg in (cfg.get("parts") or []):
                if isinstance(_seg, dict) and "literal" not in _seg and _seg.get("col"):
                    cols.update(_flat_cols(_seg.get("col")))
                elif isinstance(_seg, str):
                    cols.update(_flat_cols(_seg))
        elif rt == "CONDITIONAL":
            cols.update(_flat_cols(cfg.get("if_column")))
            # ``then`` / ``else`` may build the result from other columns.
            cols |= _interpolated_columns(cfg.get("then"), cfg.get("else"))
        elif rt in ("CASE_WHEN", "SUFFIX_WHEN"):
            cols |= _branch_columns(cfg.get("branches"))
            if rt == "CASE_WHEN":
                # A branch's ``then`` (and the top-level ``default``) can name other
                # columns via ``{Column}`` interpolation — the Taxpayer-ID rule maps
                # each country to a DIFFERENT source column that way. Collect them or
                # the frame prunes them and the literal ``{tax_id}`` token ships.
                for _br in (cfg.get("branches") or []):
                    if isinstance(_br, dict):
                        cols |= _interpolated_columns(_br.get("then"))
                cols |= _interpolated_columns(cfg.get("default"))
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
            cols |= _branch_columns([_v]) if _v else set()
            # "unique sequence on the basis of entityid" — the key column is a
            # SOURCE column the field does not own, so it is pruned unless declared.
            cols.update(_flat_cols(cfg.get("key_column")))
    return cols


def _conversion_rule_wins(rules: list[dict] | None, directive_asof) -> bool:
    """Does this conversion's OWN rule for the field outrank the overlay's?

    The overlay is a guarantee for fields nobody has spoken about on this
    conversion, not a licence to overwrite someone who has. It began as
    supplier-only, where no conversion carried its own rules for the fields it
    covers, so a rule directive simply always ran. It now carries the analyst's
    03-Aug Customer rules — Party Type, Party Number, the site-use keys — and
    those are field names a Customer conversion very often DOES have its own rule
    for, including the CW rules seeded onto existing projects in July.

    So they are ranked the way everything else here is ranked: whichever is
    latest. A conversion rule written after the document supersedes it; one
    written before it does not. A conversion rule that cannot be placed in time
    is left alone — the same safe default as ``_person_is_newer``, which does not
    let an undated directive overrule a human. In practice every stored
    ``TransformationRule`` carries ``created_at``, so the undated case is a
    hand-built config, not a real one.
    """
    if not rules:
        return False
    if directive_asof is None:
        # The document does not say when it was written, so it cannot be shown to
        # be newer than anything. Previous behaviour for undated files.
        return True
    for r in rules:
        when = r.get("as_of")
        if when is None or when >= directive_asof:
            return True
    return False


def _self_lookup_configs(pipelines: dict, target_object: str | None) -> list[dict]:
    """Every SELF_LOOKUP config in play for this conversion, from both sources."""
    out: list[dict] = []
    for _rules in (pipelines or {}).values():
        for r in _rules or []:
            if (r.get("rule_type") or "").upper() == "SELF_LOOKUP":
                out.append(r.get("config") or {})
    try:
        from app.services.strategy_overlay import self_lookup_configs
        out.extend(self_lookup_configs(target_object))
    except Exception:                                           # noqa: BLE001
        pass
    return out


def _cross_conversion_configs(pipelines: dict) -> list[dict]:
    """Every CROSS_CONVERSION_LOOKUP config in play for this conversion.

    Each names another conversion to resolve a value from — ref_conversion_id plus
    the match/value columns of that OTHER conversion's source. Collected here so the
    index can be built once, before the row loop, exactly as SELF_LOOKUP's is.
    """
    out: list[dict] = []
    for _rules in (pipelines or {}).values():
        for r in _rules or []:
            if (r.get("rule_type") or "").upper() == "CROSS_CONVERSION_LOOKUP":
                out.append(r.get("config") or {})
    return out


async def _build_cross_index(configs: list[dict]) -> dict:
    """``{"<ref_conversion_id>:<match>-><value>": {match_value: value_value}}``.

    Loads each REFERENCED conversion's source once and indexes match->value, so a
    CROSS_CONVERSION_LOOKUP resolves against another conversion in the project the
    same way SELF_LOOKUP resolves within this one. Built here (async, with DB + file
    IO) rather than in the row-local transform, which is sync and must stay pure.
    Any one reference that cannot be loaded is skipped, not fatal — its rule then
    returns its default, which is the honest "not found".
    """
    if not configs:
        return {}
    from app.models.conversion import Conversion
    from app.services.dataset_file_store import materialize_dataset_file

    index: dict[str, dict[str, str]] = {}
    frame_cache: dict[str, pd.DataFrame] = {}
    for cfg in configs:
        ref = str(cfg.get("ref_conversion_id") or cfg.get("ref_conversion")
                  or cfg.get("conversion_id") or "").strip()
        mk, vk = cfg.get("match_column"), cfg.get("value_column")
        if not ref or not mk or not vk:
            continue
        key = f"{ref}:{mk}->{vk}"
        if key in index:
            continue
        try:
            if ref not in frame_cache:
                conv = await Conversion.get(ref)
                frames = []
                for did in (getattr(conv, "source_dataset_ids", None) or []):
                    ds = await Dataset.get(did)
                    p = await materialize_dataset_file(ds) if ds else None
                    if p:
                        frames.append(parse_tabular(str(p), file_type=ds.file_type))
                frame_cache[ref] = (pd.concat(frames, ignore_index=True)
                                    if len(frames) > 1 else
                                    (frames[0] if frames else pd.DataFrame()))
            src = frame_cache[ref]
            # Reuse the self-index builder — same match->value shape, one ref frame.
            built = _build_self_index(src, [{"match_column": mk, "value_column": vk}])
            index[key] = built.get(f"{mk}->{vk}", {})
        except Exception:                                       # noqa: BLE001
            log.exception("cross-conversion index for %s failed", key)
            continue
    return index


def _sequence_key_configs(pipelines: dict, target_object: str | None) -> list[dict]:
    """Every SEQUENCE config carrying a ``key_column``, from both rule sources."""
    out: list[dict] = []
    for _rules in (pipelines or {}).values():
        for r in _rules or []:
            cfg = r.get("config") or {}
            if (r.get("rule_type") or "").upper() == "SEQUENCE" and cfg.get("key_column"):
                out.append(cfg)
    try:
        from app.services.strategy_overlay import rule_configs_of_type
        out.extend(c for c in rule_configs_of_type(target_object, "SEQUENCE")
                   if c.get("key_column"))
    except Exception:                                           # noqa: BLE001
        pass
    return out


def _build_sequence_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised key column: {key value: 0-based ordinal}}`` over the WHOLE extract.

    Analyst, 03-Aug: "Party Number: unique sequence — ON THE BASIS OF entityid."
    SEQUENCE numbered by row index instead, so a customer with five address rows
    took five different party numbers, and the eighteen Customer sheets that
    reference the party disagreed with the one that defines it.

    Built once on the full frame for the same reason ``_build_self_index`` is: the
    other rows carrying a key are usually in a different chunk, and a per-chunk
    index would restart the numbering every 20,000 rows. Ordinals follow FIRST
    APPEARANCE, not sort order, so re-running over the same extract produces the
    same numbers — a party key that renumbers on every regenerate is worse than
    no key at all.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm: dict[str, str] = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    index: dict[str, dict[str, int]] = {}
    for cfg in configs:
        spec = cfg.get("key_column")
        names = spec if isinstance(spec, (list, tuple)) else [spec]
        for name in names:
            key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
            if not key or key in index:
                continue
            col = by_norm.get(key)
            if col is None:
                continue
            seen: dict[str, int] = {}
            for v in src[col].tolist():
                kv = "" if v is None else str(v).strip()
                if not kv or kv.lower() in ("nan", "none"):
                    continue
                if kv not in seen:
                    seen[kv] = len(seen)
                # NetSuite writes an id as "123" in one column and "123.0" in
                # another once pandas has seen a blank in it, so index both.
                if kv.endswith(".0"):
                    seen.setdefault(kv[:-2], seen[kv])
            index[key] = seen
    return index


def _group_first_key_configs(pipelines: dict, target_object: str | None,
                             mappings: list | None = None) -> list[dict]:
    """Every GROUP_FIRST_FLAG config carrying a ``key_column``, from every rule source.

    Includes the mappings' ``suggested_transformation``: a SEEDED/applied store rule
    lands there (via apply_learned), NOT as a TransformationRule, so ``pipelines`` never
    sees it. Without this the index for an index-based rule (GROUP_FIRST_FLAG, like the
    Identifying Address flag) is never built and the rule silently returns its default —
    which is exactly why Identifying Address shipped blank though the seed recorded.
    """
    out: list[dict] = []
    for _rules in (pipelines or {}).values():
        for r in _rules or []:
            cfg = r.get("config") or {}
            if (r.get("rule_type") or "").upper() == "GROUP_FIRST_FLAG" and cfg.get("key_column"):
                out.append(cfg)
    for m in (mappings or []):
        st = getattr(m, "suggested_transformation", None) or {}
        if (st.get("rule_type") or "").upper() == "GROUP_FIRST_FLAG":
            cfg = st.get("config") or {}
            if cfg.get("key_column"):
                out.append(cfg)
    try:
        from app.services.strategy_overlay import rule_configs_of_type
        out.extend(c for c in rule_configs_of_type(target_object, "GROUP_FIRST_FLAG")
                   if c.get("key_column"))
    except Exception:                                           # noqa: BLE001
        pass
    return out


def _build_group_first_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised key column: {key value: first 0-based row index}}`` over the WHOLE
    extract.

    The FIRST APPEARANCE of each key, so GROUP_FIRST_FLAG can mark exactly one row per
    group — the identifying/primary row — and blank the rest. Built once on the full
    frame for the same reason the sequence index is: a customer's other rows are usually
    in another chunk, and first-appearance ordering makes the same row win on every
    regenerate.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm: dict[str, str] = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    index: dict[str, dict[str, int]] = {}
    for cfg in configs:
        spec = cfg.get("key_column")
        names = spec if isinstance(spec, (list, tuple)) else [spec]
        for name in names:
            key = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
            if not key or key in index:
                continue
            col = by_norm.get(key)
            if col is None:
                continue
            first: dict[str, int] = {}
            for i, v in enumerate(src[col].tolist()):
                kv = "" if v is None else str(v).strip()
                if not kv or kv.lower() in ("nan", "none"):
                    continue
                if kv not in first:
                    first[kv] = i
                    if kv.endswith(".0"):
                        first.setdefault(kv[:-2], i)
            index[key] = first
    return index


def _build_city_country_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised city: ISO2}`` learned from the extract's OWN rows.

    Where a row has no country code, the rest of the file usually knows: 6,196 of
    the 7,495 NetSuite rows carry both a code and a city. Building the index from
    the data beats any bundled table, needs no model, and cannot be stale.

    Majority wins on ambiguity, and ambiguity is real — "New York" appears against
    US 48 times and CN once, "San Jose" against US 109 times and CR once. Taking
    the majority is right far more often than taking the first row encountered,
    which is what any incidental ordering would have given.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _col(name):
        return by_norm.get(re.sub(r"[^a-z0-9]", "", str(name or "").lower()))

    tally: dict[str, dict[str, int]] = {}
    for cfg in configs:
        def _pick(spec):
            for n in (spec if isinstance(spec, (list, tuple)) else [spec]):
                c = _col(n)
                if c is not None:
                    return c
            return None

        cc_col, city_col = _pick(cfg.get("country_column")), _pick(cfg.get("city_column"))
        if cc_col is None or city_col is None:
            continue
        for cc, city in zip(src[cc_col].tolist(), src[city_col].tolist()):
            cc = "" if cc is None else str(cc).strip()
            city = "" if city is None else str(city).strip()
            if not cc or not city:
                continue
            key = re.sub(r"[^a-z]", "", city.lower())
            if not key:
                continue
            tally.setdefault(key, {})
            tally[key][cc] = tally[key].get(cc, 0) + 1
    return {k: max(v.items(), key=lambda kv: kv[1])[0] for k, v in tally.items()}


def _build_city_case_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{normalised city: the spelling this extract uses most}``.

    The site key is a REQUIRED UNIQUE key, and the extract spells its cities
    inconsistently: "Hyderabad" 461 times and "HYDERABAD" 103, "Dubai" 25 and
    "DUBAI" 3. Loaded as-is, Fusion creates two sites where there is one — 427 keys
    collided with another key on capitalisation alone.

    Analyst, 30-Jul: "Keep it IN-Hyderabad for now."

    Majority spelling wins rather than a blanket title-case, because title-casing
    is wrong for real place names — "Rio de Janeiro" would become "Rio De Janeiro"
    and "McAllen" would become "Mcallen". The file already knows how it normally
    writes each city; this just makes every row agree with the majority. A city
    that appears only once has no collision to fix and is left exactly as it is.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _pick(spec):
        for n in (spec if isinstance(spec, (list, tuple)) else [spec]):
            c = by_norm.get(re.sub(r"[^a-z0-9]", "", str(n or "").lower()))
            if c is not None:
                return c
        return None

    tally: dict[str, dict[str, int]] = {}
    for cfg in configs:
        col = _pick(cfg.get("city_column"))
        if col is None:
            continue
        for v in src[col].tolist():
            v = "" if v is None else str(v).strip()
            if not v:
                continue
            key = re.sub(r"[^a-z0-9]", "", v.lower())
            if not key:
                continue
            tally.setdefault(key, {})
            tally[key][v] = tally[key].get(v, 0) + 1
    # A NON-ALL-CAPS spelling wins outright, ahead of frequency, and only then does
    # the more common form win. "Keep it IN-Hyderabad" is a statement about style as
    # well as about duplicates: an all-caps city is an entry accident, not a place
    # name, and it stays wrong even when it is the majority — ABU DHABI appears 4
    # times against Abu Dhabi once, RIO DE JANEIRO likewise. Frequency alone would
    # have kept the shouting.
    #
    # Only ever picks a spelling the file actually contains, so no place name is
    # invented; and str.title() is deliberately NOT used, because it would produce
    # "Rio De Janeiro", "Ciudad De Mexico" and "Mcallen" — it breaks 8 of the
    # Spanish and Portuguese names in this extract alone.
    def _best(counts):
        return max(counts.items(), key=lambda kv: (not kv[0].isupper(), kv[1]))[0]
    return {k: _best(v) for k, v in tally.items()}


def _city_country_configs(pipelines: dict, target_object: str | None) -> list[dict]:
    out: list[dict] = []
    for _rules in (pipelines or {}).values():
        for r in _rules or []:
            if (r.get("rule_type") or "").upper() == "CITY_COUNTRY_KEY":
                out.append(r.get("config") or {})
    try:
        from app.services.strategy_overlay import rule_configs_of_type
        out.extend(rule_configs_of_type(target_object, "CITY_COUNTRY_KEY"))
    except Exception:                                           # noqa: BLE001
        pass
    return out


def _build_self_index(src: pd.DataFrame, configs: list[dict]) -> dict:
    """``{"Match->Value": {match_value: value_value}}`` over the WHOLE extract.

    SELF_LOOKUP has never once returned a value in production, and this is why:
    it reads its index from ``ctx["self_index"]``, and NOTHING in the codebase
    built one. The rule shipped, passed its unit tests against a hand-made index,
    and returned its default on every row of every real run — which is exactly why
    Parent Supplier was empty on all 3,872 suppliers.

    Built once on the full frame rather than per chunk, because the parent row a
    child points at is very often in a different chunk; and built as a dict rather
    than scanned per row because 7,495 vendors scanned pairwise is 56 million
    comparisons. Columns are matched case- and space-insensitively: the analyst
    wrote "Internal Id", the NetSuite extract says "Internal ID", and losing the
    whole lookup to that is not a failure worth having.
    """
    if src is None or not configs or not len(src.columns):
        return {}
    by_norm = {}
    for c in src.columns:
        by_norm.setdefault(re.sub(r"[^a-z0-9]", "", str(c).lower()), c)

    def _col(name):
        return by_norm.get(re.sub(r"[^a-z0-9]", "", str(name or "").lower()))

    index: dict[str, dict[str, str]] = {}
    for cfg in configs:
        mk, vk = cfg.get("match_column"), cfg.get("value_column")
        key = f"{mk}->{vk}"
        if key in index:
            continue
        mc, vc = _col(mk), _col(vk)
        if mc is None or vc is None:
            continue
        pairs: dict[str, str] = {}
        for a, b in zip(src[mc].tolist(), src[vc].tolist()):
            ka = "" if a is None else str(a).strip()
            if not ka or ka.lower() in ("nan", "none"):
                continue
            # First win: a duplicated key is a data problem, and quietly taking the
            # last row's value would make the result depend on row order.
            pairs.setdefault(ka, "" if b is None else str(b).strip())
            # NetSuite writes ids as "123" in one column and "123.0" in another once
            # pandas has seen a blank, so index the integral spelling too.
            if ka.endswith(".0"):
                pairs.setdefault(ka[:-2], pairs[ka])
        index[key] = pairs
    return index


class _RowWithTargets:
    """A per-row context that can also see the TARGET columns already computed.

    A rule whose condition names another TARGET field could not work at all. The
    per-row context is built from SOURCE columns, so ``row.get("Party Type")``
    returned None on every row, silently, and the rule fell through to its default.
    Three 31-Jul issues are that one fact:

        row 36  "Cannot apply transformation logic where the value of a target field
                 (Party Number) depends on the value of another target field
                 (Party Type)."
        row 23  Party Type "still shows blank rows instead of default as ORGANIZATION"
        row 16/22  "Tried using custom transformation rule, but its not working"

    It is the same shape as BLANK_IF_EQUALS, which had to be lifted out of the
    row-local engine entirely for exactly this reason.

    Targets are consulted only where the SOURCE has no column of that name, so this
    is purely additive: every rule that resolves today resolves identically, and only
    the lookups that used to return nothing now find a value. Fields are computed in
    target-sequence order, so a rule can read any field that precedes it — which is
    the order Oracle's own templates put dependencies in (Party Type is column 4,
    Party Number column 5).
    """
    __slots__ = ("_src", "_tgt", "_i")

    def __init__(self, src_row: dict, targets: dict, i: int):
        self._src = src_row
        self._tgt = targets
        self._i = i

    def get(self, key, default=None):
        v = self._src.get(key, _MISSING)
        if v is not _MISSING:
            return v
        col = self._tgt.get(key)
        return col[self._i] if col is not None else default

    def __getitem__(self, key):
        v = self.get(key, _MISSING)
        if v is _MISSING:
            raise KeyError(key)
        return v

    def __contains__(self, key):
        return key in self._src or key in self._tgt

    # ITERATION, which a dict has and this did not — and its absence was not a
    # missing convenience, it was a crash.
    #
    # A rule that asks "which of these column names does this row have?" writes
    # `{norm(k): k for k in row}`. With no __iter__, Python falls back to the LEGACY
    # sequence protocol: it calls row[0], row[1], … until IndexError. __getitem__
    # raised KeyError(0) instead, so the loop blew up on its first step — and because
    # generation runs in a background worker, it surfaced as a conversion that simply
    # never produced output. Supplier Site and Supplier Site Assignment both use the
    # site-key rule that iterates the row, which is exactly why those two of six sat
    # at "mapping_suggested" while the other four generated.
    #
    # Source keys first, then any TARGET column the source does not already have, so
    # iteration order matches what get() resolves: a source column of the same name
    # wins, and nothing is yielded twice.
    def __iter__(self):
        seen = set()
        for k in self._src:
            seen.add(k)
            yield k
        for k in self._tgt:
            if k not in seen:
                yield k

    def keys(self):
        return list(self)

    def __len__(self):
        return sum(1 for _ in self)


_MISSING = object()


def _transform_frame(
    src: pd.DataFrame, sorted_mappings: list, fields_by_id: dict, pipelines: dict,
    context_cols: set[str] | None = None, target_object: str | None = None,
    self_index: dict | None = None, city_country: dict | None = None,
    city_case: dict | None = None, row_offset: int = 0,
    sequence_index: dict | None = None, source_label: str = "",
    cross_index: dict | None = None, group_first_index: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Pure, row-local transform of one source (chunk) frame → target columns.

    Synchronous and CPU-bound (run via ``asyncio.to_thread``). Contains NO
    cross-row state — running it per chunk and concatenating is byte-identical to
    running it on the whole frame. Sequence numbering and whole-column-blank
    control defaults are applied later on the full frame in ``_apply_control_defaults``.

    ``context_cols`` are extra source columns (referenced by CASE_WHEN/CONCAT/
    COALESCE rules but not themselves a mapped cell) that must appear in the per-row
    context so those rules can read them.
    """
    out_cols: dict[str, list[Any]] = {}
    lineage: dict[str, dict[str, Any]] = {}
    _rule_ctx = {"self_index": self_index or {}, "city_country": city_country or {},
                 "city_case": city_case or {}, "sequence_index": sequence_index or {},
                 "cross_index": cross_index or {}, "group_first_index": group_first_index or {}}
    n_rows = len(src)
    needed_cols = {
        m.source_column for m in sorted_mappings
        if m.source_column and m.source_column in src.columns
    }
    ctx_all = list(needed_cols | {c for c in (context_cols or set()) if c in src.columns})
    # Case/punctuation-insensitive column lookup — the analyst types "City", the
    # extract may say "city" or "Bill To City", and losing a whole rule to one
    # capital letter is not a failure worth having.
    _norm_col_lookup = {_norm_hdr(c): c for c in src.columns}
    col_cache: dict[str, list[Any]] = {c: src[c].tolist() for c in needed_cols}
    records: list[dict] | None = None

    def _records() -> list[dict]:
        """The per-row context, built once and shared by both rule paths.

        It used to be built in two places — inside the pipeline branch and again
        inside the overlay branch — and only one of them added the pseudo-column
        below, so an overlay rule reading it saw nothing on a conversion that had
        no rules of its own. Which is every Customer conversion the 03-Aug
        document is meant to reach.

        ``__source_sheet`` — WHICH SOURCE the row came from. Analyst, 03-Aug:
        Party Site Use Type is "BILL_TO if addr 1, 2, 3 is taken from the sheet
        Customer_Billing_Address and SHIP_TO if taken from
        Customer_Shipping_Address", and the _B / _S suffix on the site-use keys is
        the same test. That is a fact about the FILE, not about any cell in the
        row, so the rule could not be written at all before this: the earlier
        version read the Default Billing / Default Shipping flags instead, which
        mark the DEFAULT address rather than the sheet, so a customer's second
        billing address was neither.

        A pseudo-column rather than a real one: it never reaches the output frame,
        and it cannot collide with a source column because no extract has a header
        beginning "__".
        """
        nonlocal records
        if records is None:
            records = (src[ctx_all].to_dict("records") if ctx_all
                       else src.to_dict("records"))
            if source_label:
                for _rec in records:
                    _rec["__source_sheet"] = source_label
        return records
    for m in sorted_mappings:
        tgt = fields_by_id.get(m.target_field_id)
        if not tgt:
            continue
        # DFF/attribute columns are never populated. Analyst 28-Jul: "no attribute
        # shouldn't be populated, remove all that" / "it should be empty" — raised
        # as repeat feedback ("this one is already I informed"). Enforced here, at
        # the single point every value passes through, because a per-field
        # suppression learning cannot express the wildcard: the templates carry
        # ATTRIBUTE1..30, ATTRIBUTE_CATEGORY, ATTRIBUTE_DATE/NUMBER/TIMESTAMP n and
        # GLOBAL_ATTRIBUTE*. Blocking at map time alone would still let a stray
        # default or gold value through, so it is blocked at WRITE time.
        if _is_attribute_column(tgt.field_name):
            continue
        # Statuses that DISCARD the mapped source column:
        #   not_applicable — the gold/user marked the field "leave blank".
        #   rejected       — the user threw the suggested source column away.
        # Both are normally skipped entirely, UNLESS an explicit default_value was
        # attached (e.g. Invoice Match Option = "Receipt"); an explicit default is
        # intent to populate, so it is emitted as a CONSTANT.
        # Critically, a discarded mapping must never read from source_column: it
        # still wins the per-target dedup (so a stale "suggested" row can't
        # resurrect it), and before this guard a "rejected" mapping was selected as
        # the field's mapping and then still wrote that column's values into the
        # FBDI — the UI and the mapping CSV said "rejected" while the output
        # carried the rejected column.
        _discarded = m.status in ("not_applicable", "rejected")
        # ...but a DERIVED field has no source column by nature, and its mapping
        # row is routinely not_applicable for exactly that reason. Skipping here
        # meant the strategy overlay never ran for it — the `continue` fires before
        # the overlay block below — so a rule that computes a value from other
        # columns could never write one.
        #
        # Found by reading the live instance rather than the code: Delivery Method's
        # mapping is not_applicable with no source, so every fix made to that rule
        # today would still have produced an empty column. The same trap was set for
        # Supplier Site (CONCAT), Parent Supplier (SELF_LOOKUP) and Tax Organization
        # Type (CASE_WHEN) the moment any of them was marked not_applicable.
        #
        # A BLANK directive still skips, because blank is what discarding means.
        _ov_early = _strategy_directive(target_object, tgt.field_name)
        _ov_writes = bool(_ov_early and ("rule" in _ov_early or "constant" in _ov_early))
        # ...and the analyst's OWN transformation rules are the third thing that can
        # write a derived column. They were read AFTER this guard, so a rule the
        # analyst authored in the UI never ran on a field whose mapping is
        # not_applicable — which is precisely what a derived field's mapping is.
        # CW_Issues, twice, in the analyst's own status column: "Tried using custom
        # transformation rule, but its not working" (rows 16 and 22). The strategy
        # overlay was rescued from this trap on 30-Jul; the rules the analyst types
        # were left in it.
        rules = list(pipelines.get(tgt.id, []))
        # The rules the ANALYST authored, captured before the engine's own
        # suggested_transformation is appended below. A suggestion is a guess and
        # must not count as somebody having spoken.
        _authored_rules = list(rules)
        if (_discarded and not _ov_writes and not rules
                and not (m.default_value and str(m.default_value).strip())):
            continue
        if m.suggested_transformation and not rules and m.status != "rejected":
            rules.append({"rule_type": m.suggested_transformation.get("rule_type"),
                          "config": m.suggested_transformation.get("config", {})})
        dv = m.default_value
        has_src = bool(m.source_column) and m.source_column in col_cache and not _discarded
        # The RULE's own source column, when the mapping has none. The rule dialog
        # asks for it ("the legacy column this rule transforms") and generation never
        # read it — so "Address Name <- City, value mapping" fed the rule an empty
        # string on every row. The mapping still wins where it has one: a person
        # binding the field in Mapping Review is the more specific statement.
        _rule_src = None
        if not has_src:
            for _r in rules:
                _c = _r.get("source_column")
                if _c and _c in src.columns:
                    _rule_src = _c
                    break
                if _c:
                    _c2 = _norm_col_lookup.get(_norm_hdr(_c))
                    if _c2 is not None:
                        _rule_src = _c2
                        break
        if rules:
            # A transform rule (CASE_WHEN / COALESCE / CONCAT / …) can derive its
            # value from OTHER columns via the per-row context, so it must run even
            # when THIS target has no single source_column. The rule — including its
            # own configured default (e.g. a CASE_WHEN default of "") — is
            # authoritative: we do NOT overlay the mapping-level default_value on a
            # rule result, so an intentional blank is never clobbered by a stray
            # constant default (which was turning a Delivery Channel/Method
            # CASE_WHEN into a constant "EMAIL" whenever source_column was null).
            records = _records()
            src_vals = (col_cache[m.source_column] if has_src
                        else (src[_rule_src].tolist() if _rule_src else None))
            # row_index is the GLOBAL row number. It was never passed here at all —
            # only the mapping PREVIEW endpoint set it — so SEQUENCE read
            # ctx.get("row_index", 0) and returned start+0 for every row. Party
            # Number, a required UNIQUE key, would have shipped NXT000001 on all
            # 5,489 rows while the preview showed a correct running sequence. And it
            # has to be global, not chunk-local, or the numbering restarts every
            # 20,000 rows and the 18 sheets that reference it stop agreeing.
            col_values = [
                apply_pipeline(rules, (src_vals[i] if src_vals is not None else ""),
                               row=_RowWithTargets(records[i], out_cols, i),
                               ctx={**_rule_ctx, "row_index": row_offset + i})
                for i in range(n_rows)
            ]
            # A NEWER FIXED VALUE BEATS AN OLDER RULE. (05-Aug)
            #
            # The paragraph above says the rule is authoritative and the mapping's
            # default_value is never overlaid on a rule result. That protects an
            # intentional blank — a CASE_WHEN whose own default is "" must not be
            # turned into a constant by a stray default. It is right about the
            # danger and silent about the date, which is the same mistake the
            # authorship test made one branch below.
            #
            # Receipt Routing, measured live on Supplier Site: a VALUE_MAP dated
            # 03-Aug 18:32 maps "Direct Delivery"->3, "Inspection Required"->2,
            # "Standard Receipt"->1, default "". It has NO source column, so it
            # evaluated against "" on every row and returned its default — a blank
            # column, 198 rows of nothing. The analyst then typed the fixed value 3
            # and approved it at 04-Aug 10:57, SIXTEEN HOURS LATER. That value was
            # skipped because a rule existed, the column stayed blank, and the
            # strategy overlay filled every blank with DIRECT.
            #
            # The screen showed "Filled with a constant default 3" and a rule badge
            # at the same time, both true, fighting each other, with no indication
            # which would reach the file.
            #
            # So the same rule as everywhere else: whichever is latest. A fixed
            # value that is approved and NEWER than every rule on the field wins
            # outright — "write one constant into this column for every row" is
            # what the box the analyst typed into says it does. A rule authored
            # after the value still wins, so a freshly written CASE_WHEN is safe
            # from a stale captured default, which is the case the original comment
            # was defending.
            #
            # An undated rule loses to a dated approval, for the same reason an
            # undated approval loses to a dated directive: it cannot be shown to be
            # the later statement. The engine's own suggested_transformation is
            # appended to `rules` unstamped, and a guess should never outrank a
            # value a person approved.
            _rule_dates = [r.get("as_of") for r in rules if r.get("as_of")]
            _newest_rule = max(_rule_dates) if _rule_dates else None
            _dv_at = getattr(m, "approved_at", None)
            if (dv is not None and str(dv).strip()
                    and m.status in ("approved", "overridden")
                    and _dv_at is not None
                    and (_newest_rule is None or _dv_at > _newest_rule)):
                col_values = [dv] * n_rows
        elif has_src:
            src_vals = col_cache[m.source_column]
            col_values = [
                (dv if (v is None or str(v).strip() == "") and dv is not None else v)
                for v in src_vals
            ]
        else:
            col_values = [dv or ""] * n_rows
        # ── Strategy overlay (write-time guarantee) ──────────────────────
        # Seeded learnings demonstrably did NOT reach the output — see
        # strategy_overlay for the evidence. Enforce the analyst rules here,
        # after mapping, where nothing downstream can undo them.
        _ov = _ov_early          # resolved above, before the discard check
        # An analyst who APPROVED a source column for this field has overruled the
        # strategy constant, and overwriting it produced the reported bug: Tax
        # Organization Type showed CORPORATION on every row despite being mapped
        # and approved. Same guard as _apply_control_defaults (QA #8), which this
        # overlay was written after and never inherited.
        #
        # "suggested" deliberately does NOT count: auto-map guessing is exactly
        # what the strategy constants exist to correct, so only a deliberate
        # approve/override wins.
        #
        # ...but "approved" is not proof a PERSON approved it. The learning engine
        # approves the mappings it applies, under approved_by="learning-engine",
        # and those rows were passing this guard — so a SEEDED mapping outranked
        # the analyst's own later correction. That is why three fields the 30-Jul
        # corrections declare BLANK still shipped values in the file the analyst
        # sent back: Supplier Name New carried the supplier name on all 3,872 rows,
        # Procurement BU carried "Nextracker Consolidated" on 5,315, and Liability
        # Distribution carried an account string on 1,528. Each had a seeded source
        # column, and each therefore skipped its own blank rule.
        #
        # The line is the one the analyst drew on 30-Jul: a person editing and
        # approving in the UI outranks everything; an engine approval does not
        # outrank the person who wrote the correction. Same test as
        # learning_service._eligible, which already had to draw it.
        _approver = str(getattr(m, "approved_by", "") or "").strip()
        _by_a_person = bool(_approver) and _approver != "learning-engine"
        # AUTHORSHIP IS PROVENANCE. THE DATE DECIDES. (05-Aug)
        #
        # The paragraph above is still the right instinct and was still the wrong
        # rule, because it made authorship decisive rather than the date — which is
        # the opposite of what this architecture says about itself:
        #
        #     "Every statement about how a field maps is a dated entry ...
        #      Newest wins; authorship is provenance only."
        #
        # Measured live on NextPower Supplier Test / Supplier Site: ALL SEVEN
        # strategy constants were overriding the value the screen showed, and three
        # of them differed — Receipt Routing showed 3 and shipped DIRECT, Invoice
        # Match Option showed R and shipped Receipt, Match Approval Level showed 3
        # and shipped 3-Way. Every one of those rows was approved, carried a fixed
        # value, and was dated 03/04-Aug. The directive that beat them is dated
        # 13-JUL — three weeks older — and it won purely because the newer statement
        # was stamped "learning-engine".
        #
        # The analyst reported it as "the mappings in the UI do not reach the
        # output", across supplier, customer, BOM, Item and Employee. It is one
        # rule, in one place, and this is it.
        #
        # WHAT STILL PROTECTS THE CASE THE OLD RULE WAS ADDED FOR. That case was a
        # SEEDED row re-populating a field a correction had declared blank — and it
        # is a date question too: the seed carried the date it was seeded, which was
        # older than the correction, so under this rule it loses on its own merits.
        # The comparison below is unchanged; only the authorship requirement is
        # gone. A row still has to carry a real statement, still has to be approved
        # or overridden, and still has to be NEWER than the directive it beats.
        #
        # An undated row cannot be shown to be newer and therefore does not win —
        # see _person_is_newer, which requires an actual timestamp. That is what
        # stops an old seeded row with no approval date from resurfacing.
        _decision_outranks_directive = bool(_approver) or _by_a_person
        # ...and WHICHEVER IS LATEST. Analyst, 30-Jul, stating the precedence in
        # full: "1) analyst manually changed or present in mapping file (whichever
        # is latest) 2) learnings and golden records from database 3) AI".
        #
        # So a person's approval does not win forever. It wins until the analyst
        # issues a newer instruction in a rule file, at which point the file is the
        # more recent statement of the same person's intent. Each rule file now
        # carries _effective_date and each directive carries it as `as_of`; a
        # directive with no date loses to any human approval, which is the previous
        # behaviour and the safe default for older files.
        _asof = (_ov or {}).get("as_of") if _ov else None
        _appr_at = getattr(m, "approved_at", None)
        _person_is_newer = True
        if _asof is not None:
            # No approval timestamp means we cannot show the person spoke later,
            # and the dated file is the only thing that can be placed in time.
            _person_is_newer = bool(_appr_at) and _appr_at >= _asof
        # A PERSON'S FIXED VALUE COUNTS, not only a person's source column.
        #
        # This read `m.source_column` alone, so the only analyst it could see was
        # one who had bound a COLUMN. Setting a constant — "Receipt Routing = 3",
        # typed into the Fixed value box and approved — leaves source_column null
        # by definition, so `_explicit` was False and the strategy constant below
        # replaced every row with its own value.
        #
        # Reported live on NextPower Supplier Test (Supplier Site): Receipt
        # Routing set to 3, Invoice Match Option to R, Match Approval Level to 3,
        # all approved and showing "currently 3" on screen — and the file shipped
        # DIRECT, Receipt and 3-Way, the 13-Jul strategy values. The screen and
        # the file disagreed and the screen looked right, which is the single most
        # expensive shape of bug in this tool.
        #
        # Typing a constant and approving it is exactly as deliberate as binding a
        # column, and the analyst's own precedence rule — "analyst manually
        # changed OR the mapping file, whichever is latest" — draws no distinction
        # between the two. The date test below is unchanged: their value wins
        # while it is the later one, and a directive issued after it still wins.
        _person_set_a_value = bool(str(m.source_column or "").strip()
                                   or str(m.default_value or "").strip())
        # AUTHORING A RULE IS SPEAKING TOO.
        #
        # _explicit was computed from the MAPPING's status, so a custom rule typed
        # against a field whose mapping still sat at "suggested" was not protected
        # — a strategy constant replaced every row and took the rule's output with
        # it. That is the same complaint as the constants above, one door along:
        # the analyst did something deliberate and the file did not show it.
        #
        # A rule has no status to approve, so requiring one was the bug. It has a
        # DATE, though, and date is how everything here is ranked — so it is
        # ranked the same way the rule directive already ranks it: a rule written
        # after the document wins, one written before it does not.
        _authored_rule_wins = bool(_authored_rules) and _conversion_rule_wins(
            _authored_rules, _asof)
        # `_decision_outranks_directive` replaces the old `_by_a_person` here. The
        # row must still SAY something, must still be approved or overridden, and
        # must still be newer than the directive — what it no longer has to be is
        # signed by a human. See the note beside its definition for why.
        _explicit = bool(_person_set_a_value
                         and m.status in ("approved", "overridden")
                         and _decision_outranks_directive
                         and _person_is_newer) or _authored_rule_wins
        if _ov:
            if _ov.get("blank") and not _explicit:
                col_values = [""] * n_rows
            elif "constant" in _ov:
                cv = _ov["constant"]
                # Filling blanks is always safe — it adds a value where the mapped
                # column had none, which is what the constant is for. Replacing
                # every row is the part an explicit mapping overrules.
                col_values = ([v if str(v).strip() else cv for v in col_values]
                              if (_ov.get("fill_blank_only") or _explicit)
                              else [cv] * n_rows)
            elif "rule" in _ov and not _conversion_rule_wins(rules, _asof):
                records = _records()
                col_values = [apply_pipeline([_ov["rule"]], col_values[i],
                                             row=_RowWithTargets(records[i], out_cols, i),
                                             ctx={**_rule_ctx, "row_index": row_offset + i})
                              for i in range(n_rows)]
        # COLLISION GUARD — one bare field name, many interface sheets.
        #
        # out_cols is keyed by field NAME, but Oracle repeats a field name across
        # sheets and each sheet is a SEPARATE target field with its OWN pipeline:
        # "Relationship Source System Reference" is on many Customer tabs, and only
        # the PARTYSITES copy carries the CONCAT rule (entityid_internalid_RS). A
        # sheet whose copy of the field is UNMAPPED produces a blank column; if it is
        # processed AFTER the one that computed the real value, `out_cols[name] =
        # blank` erased the value and the field shipped empty although the UI showed
        # the rule — the reported "mapped in the UI, blank in the output".
        #
        # So a blank, unmapped same-named field must not overwrite a value another
        # sheet's mapping or rule already produced. A real value still wins normally
        # (the test below is only "new is entirely blank AND something populated is
        # already there"), so a sheet with its own populated mapping is unaffected,
        # and last-populated-wins is preserved between two real values.
        _new_blank = all(v is None or str(v).strip() == "" for v in col_values)
        _prev = out_cols.get(tgt.field_name)
        if _new_blank and _prev is not None and any(str(v).strip() for v in _prev):
            continue
        out_cols[tgt.field_name] = col_values
        lineage[tgt.field_name] = {"source_column": m.source_column, "default_value": m.default_value,
                                   "rules": rules, "status": m.status, "confidence": m.confidence,
                                   "strategy_overlay": bool(_ov),
                                   # Why a strategy constant did NOT take effect —
                                   # otherwise "the overlay ran" and "the overlay
                                   # was overruled" look identical in the trace.
                                   "strategy_overruled_by_mapping": bool(_ov and _explicit)}
    return pd.DataFrame(out_cols), lineage


from app.services.merge_dedupe import merge_dedupe as _merge_dedupe  # noqa: E402


def _merge_dedupe_frames(frames: list[pd.DataFrame], target_object: str | None) -> pd.DataFrame:
    """Converge per-source converted frames into one, de-duplicated by the object's
    natural business key with source priority. Delegates to the unit-tested
    merge_dedupe module, passing the natural-key registry."""
    return _merge_dedupe(frames, target_object, REFERENCE_KEY_FIELDS)


def route_frame(wanted: Any, src_frames: dict | None,
                fallback: pd.DataFrame) -> pd.DataFrame:
    """Pick the bound source frame that supplies the most of ``wanted`` columns.

    ``src_frames`` is the ``collect_frames`` dict produced by
    ``build_converted_dataframe``: ``{dataset_id: (converted_frame, source_columns)}``.
    Note the KEY is a dataset id, never an interface-sheet name — routing is decided
    by which file actually contains the columns a sheet needs, and a caller that
    treats those keys as sheet names silently matches nothing.

    Ties and no-evidence cases return ``fallback`` (the merged frame), which is the
    single-source behaviour. Extracted from generation so the required-field gate
    routes identically instead of re-deriving it — §9.15 and the strategy overlay
    are the standing lesson that a second copy of a rule drifts from the first.
    """
    if not src_frames:
        return fallback
    wanted = {str(w).strip().lower() for w in (wanted or []) if w is not None}
    if not wanted:
        return fallback
    best, best_hits = None, 0
    for _did, entry in src_frames.items():
        _odf, _cols = entry if isinstance(entry, tuple) else (entry, [])
        have = {str(c).strip().lower() for c in (_cols or [])}
        hits = sum(1 for w in wanted if w in have)
        if hits > best_hits:
            best, best_hits = _odf, hits
    return best if best is not None and best_hits > 0 else fallback


async def build_converted_dataframe(
    conversion: Conversion, max_rows: int | None = None,
    collect_frames: dict | None = None,
    carry_source_cols: list[str] | None = None,
    enrich_by_entityid: dict | None = None,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    # ``enrich_by_entityid``: {source col -> {entityid -> value}} joined onto the raw
    # source by entityid before conversion, so a column that lives on a DIFFERENT
    # customer source file (person names in the contact file, startdate/datecreated in
    # the master) is present where a rule needs it. Only fills a column the frame lacks
    # or has entirely blank — see customer_merge.enrich_source_frame. Customer-only; off
    # for every other object.
    """``collect_frames``: when a dict is passed, it is populated with
    ``{dataset_id: (converted_frame, source_columns)}`` for every bound source —
    BEFORE they are merged. Generation uses this to route each Oracle interface
    sheet to the source sheet that actually feeds it, which a merged frame cannot
    express when the sources have different row grains (e.g. 5,489 customers vs
    22,505 addresses in one workbook). All other callers ignore it and keep the
    existing merged-frame behaviour.

    ``carry_source_cols``: source column names to thread through onto the converted
    frame as ``__<name>`` (e.g. ``entityid``). The transform is row-local, so the
    converted frame is 1:1 with the source in order; the raw source value is copied
    across by position. Used by the multi-source Customer merge to keep the customer
    key for grain-aware sheet splitting and entityid linkage. Off by default, so
    every other object is byte-for-byte unchanged.
    """
    # Source rows come from the uploaded file(s) (dataset mode) or are streamed
    # live from Oracle EBS (EBS mode — no dataset). A module/target object can now
    # be fed by SEVERAL source files (priority order): each is converted with the
    # same mappings, then the converted frames are merged + de-duplicated into one.
    # ``max_rows`` caps the work for previews (only the shown rows are generated).
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    mappings = await MappingSuggestion.find(MappingSuggestion.conversion_id == conversion.id).to_list()
    # Dedupe: a target field can end up with more than one MappingSuggestion (an
    # auto-map "suggested" row plus an approved/overridden one, e.g. after prompt
    # steering or learning). Keep the highest-priority mapping per target field so
    # the output uses the APPROVED source, not whichever row was processed last.
    mappings = list(best_mapping_by_target(mappings).values())

    # Memory: the source extract can be very wide (e.g. Customer is 234 columns),
    # but the transform only reads the columns that are actually mapped. Drop the
    # rest right after load so peak memory is bounded to the mapped slice — a wide
    # source held in full is what pushed the heavy Customer generate over the
    # free-tier limit (the request died at the gateway and the browser reported it
    # as a CORS error). Rule contexts reference source columns too, so keep any
    # column named by a mapping OR a transformation rule config.
    # Columns referenced by a seeded/applied transform (CASE_WHEN if_column,
    # CONCAT/COALESCE columns) arrive on the mapping's suggested_transformation and
    # may not be a mapped cell themselves — keep them so the derivation can read them.
    _ref_from_sugg: set[str] = set()
    for _m in mappings:
        _st = getattr(_m, "suggested_transformation", None)
        if _st:
            _ref_from_sugg |= _rule_referenced_columns(
                [{"rule_type": _st.get("rule_type"), "config": _st.get("config", {})}]
            )
    needed_src = {m.source_column for m in mappings if m.source_column} | _ref_from_sugg
    # Columns to thread through verbatim (the customer key for the merge) must
    # survive the wide-source pruning below even when no mapping/rule reads them.
    _carry = [str(c) for c in (carry_source_cols or []) if c]
    if _carry:
        needed_src |= set(_carry)
    # Cross-grain enrichment columns (Customer only: person names from the contact
    # file, startdate/datecreated from the master) are JOINED onto the raw source by
    # entityid below. Keep them through the wide-source pruning even when nothing on
    # THIS conversion names them yet, so a mapping/rule that consumes them (Person
    # First/Middle/Last, Party Site From Date) actually finds a populated column.
    if enrich_by_entityid:
        needed_src |= {str(k) for k in enrich_by_entityid.keys()}

    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list() if template else []
    fields_by_id = {f.id: f for f in fields}

    rules_list = await TransformationRule.find(
        TransformationRule.conversion_id == conversion.id
    ).sort(+TransformationRule.sequence).to_list()

    pipelines: dict = {}
    for r in rules_list:
        if r.target_field_id:
            pipelines.setdefault(r.target_field_id, []).append(
                # source_column is a FIELD ON THE RULE, and it was being dropped here.
                # The rule dialog asks "Source column — the legacy column this rule
                # transforms", stores it, and shows it back in the saved-rules banner;
                # generation then fed the rule the MAPPING's source value instead and
                # ignored the rule's own column entirely. With no mapped source that is
                # an empty string, so a VALUE_MAP on Address Name <- City evaluated
                # against "" on every row and returned its default. Shipped, visible in
                # the UI, and inert.
                # `as_of` — when THIS conversion's rule was written. Needed since
                # the write-time overlay started carrying rules for Customer as
                # well as Supplier: without a date the two cannot be ranked, and
                # the overlay would run on top of a rule the analyst typed in the
                # UI five minutes ago. "Whichever is latest" is the analyst's own
                # precedence rule and it has to be answerable for rules too, not
                # only for constants and suppressions.
                {"rule_type": r.rule_type, "config": r.rule_config or {},
                 "source_column": r.source_column,
                 "as_of": getattr(r, "created_at", None)}
            )

    # Extra per-row context columns for rule evaluation: suggested-transform refs
    # plus any referenced by explicit transformation-rule pipelines.
    _ctx_cols: set[str] = set(_ref_from_sugg)
    for _rules in pipelines.values():
        _ctx_cols |= _rule_referenced_columns(_rules)
    # The enriched columns must also reach the per-row rule dict (Party Type reads
    # firstname/lastname; Party Site From Date reads startdate/datecreated).
    if enrich_by_entityid:
        _ctx_cols |= {str(k) for k in enrich_by_entityid.keys()}

    # Order mappings by target field sequence once (metadata — cheap, row-count
    # independent). The heavy per-column transform runs on row CHUNKS in a worker
    # thread (asyncio.to_thread) so it never blocks the event loop and peak memory
    # stays bounded to one chunk. The transform is row-local, so chunk-then-concat
    # is byte-identical to a single pass.
    sorted_mappings = sorted(
        mappings,
        key=lambda m: (fields_by_id.get(m.target_field_id).sequence if fields_by_id.get(m.target_field_id) else 0),
    )
    # Object name used to resolve the write-time strategy overlay. Prefer the
    # template's business object (the precise interface, e.g. "Supplier Address")
    # and fall back to the conversion's own target object.
    _tpl_ov = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    _obj_name_for_overlay = ((_tpl_ov.business_object if _tpl_ov else None)
                             or getattr(conversion, "target_object", None) or "")

    # The overlay's OWN rules read source columns, and nothing was telling the
    # frame about them. Both halves of the pipeline drop a column nobody claims:
    # `needed_src` prunes it out of the DataFrame, and `_ctx_cols` leaves it out of
    # the per-row dict. So every strategy rule that derives from an unmapped column
    # silently evaluated against blanks and produced its default.
    #
    # That is why Supplier Site came out empty on all 8,561 rows even though its
    # rule is CONCAT("Country Code", "City") and BOTH columns exist in the NetSuite
    # extract — the rule was reading a row that had neither. The transformation-rule
    # pipelines had been given this treatment; the overlay was added later and never
    # inherited it, which is the same way this codebase has lost a guarantee before.
    try:
        from app.services.strategy_overlay import referenced_columns as _ov_ref_cols
        _overlay_cols = _ov_ref_cols(_obj_name_for_overlay)
        _ctx_cols |= _overlay_cols
        needed_src |= _overlay_cols
    except Exception:  # noqa: BLE001 — never fail generation over the overlay
        log.exception("could not collect strategy-overlay source columns")

    # Cross-conversion lookups resolve against OTHER conversions, so the index is
    # built ONCE for the whole conversion (independent of which source frame is being
    # converted) and closed over by _convert_source. Empty when no rule needs it.
    _cross_idx = await _build_cross_index(_cross_conversion_configs(pipelines))

    async def _convert_source(src: pd.DataFrame,
                              label: str = "") -> tuple[pd.DataFrame, dict]:
        """Prune to the mapped/referenced columns, then chunk-transform ONE source
        frame into the target field-keyed output frame.

        ``label`` names WHERE this frame came from — the dataset's own name, which
        for a workbook sheet extracted on upload is "<file> — <sheet>". It reaches
        the rules as the pseudo-column ``__source_sheet``, which is how BILL_TO /
        SHIP_TO and the _B / _S key suffixes are decided.
        """
        try:
            if needed_src and len(src.columns) > len(needed_src) + 4:
                keep = [c for c in src.columns if c in needed_src]
                if keep:
                    src = src[keep].copy()
        except Exception:  # noqa: BLE001 — pruning is an optimization, never fatal
            pass
        # Built on the FULL frame before chunking — a child's parent is usually in
        # another chunk, so a per-chunk index would resolve some rows and not others.
        _self_idx = _build_self_index(
            src, _self_lookup_configs(pipelines, _obj_name_for_overlay))
        _ccfg = _city_country_configs(pipelines, _obj_name_for_overlay)
        _city_idx = _build_city_country_index(src, _ccfg)
        _city_case = _build_city_case_index(src, _ccfg)
        _seq_idx = _build_sequence_index(
            src, _sequence_key_configs(pipelines, _obj_name_for_overlay))
        # First-appearance row per key, so GROUP_FIRST_FLAG marks one row per group
        # (the identifying address). Full-frame, first-appearance — same as sequence.
        _gf_idx = _build_group_first_index(
            src, _group_first_key_configs(pipelines, _obj_name_for_overlay, sorted_mappings))
        n_total = len(src)
        if n_total <= _TRANSFORM_CHUNK_ROWS:
            return await asyncio.to_thread(
                _transform_frame, src, sorted_mappings, fields_by_id, pipelines, _ctx_cols,
                _obj_name_for_overlay, _self_idx, _city_idx, _city_case, 0,
                _seq_idx, label, _cross_idx, _gf_idx)
        parts: list[pd.DataFrame] = []
        lin0: dict = {}
        for start in range(0, n_total, _TRANSFORM_CHUNK_ROWS):
            chunk = src.iloc[start:start + _TRANSFORM_CHUNK_ROWS]
            odf, lin = await asyncio.to_thread(
                _transform_frame, chunk, sorted_mappings, fields_by_id, pipelines, _ctx_cols,
                _obj_name_for_overlay, _self_idx, _city_idx, _city_case, start,
                _seq_idx, label, _cross_idx, _gf_idx)
            parts.append(odf)
            if not lin0:
                lin0 = lin
        return (pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0]), lin0

    lineage: dict = {}
    source_ids = conversion.source_dataset_ids
    if source_ids:
        from app.services.dataset_file_store import materialize_dataset_file
        frames: list[pd.DataFrame] = []
        for did in source_ids:
            dataset = await Dataset.get(did)
            src_path = await materialize_dataset_file(dataset) if dataset else None
            if src_path is None:
                if len(source_ids) == 1:
                    raise ValueError("Dataset source file not found; please re-upload the dataset")
                continue  # skip an unreadable secondary source rather than fail the merge
            src = parse_tabular(str(src_path), file_type=dataset.file_type, nrows=max_rows)
            # The source's OWN columns, captured BEFORE enrichment. Grain classification
            # keys on these: the master has companyname, the address files have addr*,
            # the contact file has firstname. Enrichment (below) JOINS the borrowable
            # columns onto every frame, so classifying on the post-enrichment columns
            # would see firstname on all four sources and tag them all CONTACT — which
            # empties the party grain, so the parties sheet falls back to the whole
            # 31k-row frame (no dedup) and the party ref stays entityid. Classify on the
            # real source columns instead.
            _orig_src_cols = [str(c) for c in src.columns]
            # Cross-grain enrichment (Customer): fill this source's missing/blank
            # borrowable columns from the other source files by entityid, BEFORE the
            # transform runs, so the rules and mappings that read them stop seeing
            # blanks. Only fills what the frame lacks — real source values are never
            # overwritten (see customer_merge.enrich_source_frame). Off for every
            # other object (enrich_by_entityid is None).
            if enrich_by_entityid:
                from app.services import customer_merge as _cm_enrich
                src = _cm_enrich.enrich_source_frame(src, enrich_by_entityid)
            odf, lin = await _convert_source(
                src, str(getattr(dataset, "name", "") or
                         getattr(dataset, "file_name", "") or ""))
            # Thread the customer key through by POSITION — the transform is
            # row-local so odf is 1:1 with src in order. Guarded on equal length so a
            # shape surprise never mis-aligns the key onto the wrong rows.
            if _carry and len(src) == len(odf):
                for _cc in _carry:
                    if _cc in src.columns:
                        odf["__" + _cc] = src[_cc].astype(str).str.strip().values
            frames.append(odf)
            if collect_frames is not None:
                # Keep the source's own column list (PRE-enrichment): sheet routing and
                # grain classification decide which source feeds an interface sheet by
                # the columns the file actually contains, not the ones enrichment added.
                collect_frames[str(did)] = (odf, _orig_src_cols)
            if not lineage:
                lineage = lin
        if not frames:
            raise ValueError("No readable source files for this conversion")
        if len(frames) == 1:
            # De-duplicate a SINGLE source too. Previously dedupe only ran on the
            # multi-source merge, so duplicates WITHIN one extract passed straight
            # through — and a single-file conversion is the normal case. Same
            # rules either way: master objects collapse on the natural business
            # key, child interfaces (Site/Address/Contacts) fall back to exact-row
            # de-dup so one supplier keeps its many addresses.
            obj = (template.business_object if template else None) or conversion.target_object
            out_df = _merge_dedupe_frames(frames, obj)
        else:
            # Multi-source: converge the per-source outputs, then de-duplicate by
            # the object's natural business key with SOURCE PRIORITY (earlier source
            # wins). Master objects (unique key per source) dedupe on the key; child
            # interfaces (many rows per entity) fall back to exact-row de-dup only.
            obj = (template.business_object if template else None) or conversion.target_object
            out_df = _merge_dedupe_frames(frames, obj)
    else:
        from app.services.mapping_service import ebs_fetch_rows
        table = getattr(conversion, "ebs_table_hint", "") or ""
        rows = await ebs_fetch_rows(table) if table else []
        src = pd.DataFrame(rows)
        if max_rows:
            src = src.head(max_rows)
        out_df, lineage = await _convert_source(src)

    # ── User duplicate/cleansing decisions ───────────────────────────────────
    # Applied HERE, on the assembled frame, because this is the single point every
    # writer reads from: the CSV bundle, the plain xlsx, the filled Oracle template
    # and both project-level zip downloads all branch off `out_df`. Runs AFTER
    # automatic de-duplication (the user is adjudicating what that left behind) and
    # BEFORE LOV enforcement and the DQ report, so the report describes the file
    # that actually ships.
    from app.services.decision_service import apply_conversion_decisions
    out_df = await apply_conversion_decisions(
        out_df, conversion,
        (template.business_object if template else None) or conversion.target_object)

    # Per-interface-sheet frames follow the same decisions. A multi-source
    # conversion routes some sheets to their own source frame via `_frame_for`, so
    # filtering only the merged frame would leave excluded suppliers alive on the
    # Address and Site sheets — a partially-applied decision is a data-integrity
    # bug, not a cosmetic one.
    if collect_frames:
        _obj = (template.business_object if template else None) or conversion.target_object
        for _k, _v in list(collect_frames.items()):
            # Each entry is (converted_frame, source_column_names) — the second
            # element drives sheet routing and must be preserved as-is.
            _f, _cols = _v
            collect_frames[_k] = (
                await apply_conversion_decisions(_f, conversion, _obj), _cols)

    # Coded (LOV) columns last, on the assembled (merged) frame so the audit counts
    # distinct values across the whole file. Row-local, so it stays chunk-safe.
    if fields:
        lov_report = await asyncio.to_thread(enforce_coded_values, out_df, fields)
        for fname, rep in lov_report.items():
            lineage.setdefault(fname, {})["lov"] = rep

    return out_df, lineage


async def build_sheet_frames(
    conversion: Conversion, max_rows: int | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """``({interface sheet name: frame}, merged_frame)`` — what generation will write.

    WHY THIS EXISTS
    ---------------
    The required-field gate has to answer "does this sheet's required column hold a
    value", and three separate things can satisfy a required field: a mapped source
    column, a control default with no mapping at all, and a per-sheet route to a
    different source file. ``build_converted_dataframe`` gives none of them per
    sheet — its ``collect_frames`` is keyed by DATASET ID, so a caller that looks a
    sheet name up in it matches nothing and every required field reads as absent.
    That is precisely the 29-Jul live finding: ``required-check`` returned
    ``blocked: true`` with ``sheet_generated: false`` on every sheet of a healthy
    Supplier conversion, i.e. the gate fired 100% of the time.

    So: route each sheet the way generation routes it (``route_frame``), give it that
    sheet's own columns, and apply the same control defaults / suppression sets
    ``_finalize`` applies. Columns stay keyed by ``field_name`` (no ``*`` header
    rename) — callers normalise names anyway, and the raw name is easier to match.

    Returns ``({}, merged)`` when the conversion has no template or no sheeted
    fields; callers then fall back to the merged frame, the previous behaviour.
    A sheet the template DECLARES but which carries no fields maps to ``None``, so a
    caller can distinguish "owned but produced nothing" from "not this conversion's
    sheet at all".
    """
    from app.models.fbdi import FBDISheet

    src_frames: dict = {}
    df, _lin = await build_converted_dataframe(
        conversion, max_rows=max_rows, collect_frames=src_frames)
    if len(src_frames) < 2:
        src_frames = {}                     # single source — nothing to route

    template = (await FBDITemplate.get(conversion.template_id)
                if conversion.template_id else None)
    if template is None:
        return {}, df
    fields = await FBDIField.find(
        FBDIField.template_id == template.id).sort(+FBDIField.sequence).to_list()
    sheets = await FBDISheet.find(
        FBDISheet.template_id == template.id).sort(+FBDISheet.sequence).to_list()
    if not fields or not sheets:
        return {}, df

    by_sheet: dict[Any, list] = {}
    for f in fields:
        by_sheet.setdefault(f.sheet_id, []).append(f)

    maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id).to_list()
    best = best_mapping_by_target(maps)
    fbyid = {f.id: f for f in fields}

    def _key(f) -> str:
        return (f.field_name or "").strip().lower().rstrip("*").strip()

    suppressed = {
        _key(fbyid[tid]) for tid, m in best.items()
        if m.status == "not_applicable" and tid in fbyid and fbyid[tid].field_name
        and not (getattr(m, "default_value", None) and str(m.default_value).strip())
    }
    # A FIXED VALUE IS AN EXPLICIT MAPPING TOO — the same blind spot, a third time.
    #
    # This read `source_column` alone, exactly as the strategy-overlay guard did
    # before 05-Aug and as the rule branch did until this morning. A constant has
    # no source column — that is what makes it a constant — so a field the analyst
    # had pinned to a fixed value was NOT in `explicit`, and the control defaults
    # were free to write over it.
    #
    # It has been survivable only by luck: _CONTROL_DEFAULTS fills blank columns
    # only, and after this morning's fixes a pinned field is no longer blank by the
    # time it gets here. That is a coincidence, not a guarantee, and the two other
    # places this same test was wrong both shipped wrong values for weeks.
    explicit = {
        _key(fbyid[tid]) for tid, m in best.items()
        if tid in fbyid and fbyid[tid].field_name
        and ((m.source_column or "").strip()
             or (getattr(m, "default_value", None) is not None
                 and str(m.default_value).strip()))
        and (m.status or "") in ("approved", "overridden")
    }
    src_by_field = {m.target_field_id: str(m.source_column)
                    for m in maps if m.source_column}

    obj_name = (template.business_object or conversion.target_object or "")
    suppressed |= _strategy_blank_fields(obj_name)
    eff: dict = {}
    try:
        from app.services.defaults_service import compute_effective_defaults
        eff = (await compute_effective_defaults(
            conversion, use_ai=False)).get("defaults", {}) or {}
    except Exception:                                           # noqa: BLE001
        eff = {}

    out: dict[str, pd.DataFrame] = {}
    for s in sheets:
        sfields = by_sheet.get(s.id) or []
        if not sfields:
            # DECLARED by the template but carrying no fields, so no frame. Recorded
            # as None rather than omitted: the caller needs to tell "this conversion
            # owns the sheet and produced nothing" (a real failure) from "this sheet
            # belongs to another conversion in the bundle" (not this gate's business).
            # Omitting it made those two indistinguishable.
            out[str(s.sheet_name or "")] = None
            continue
        wanted = {src_by_field.get(f.id) for f in sfields}
        wanted.discard(None)
        cols = _dedup([f.field_name for f in sfields])
        sdf = route_frame(wanted, src_frames, df).reindex(columns=cols, fill_value="")
        sdf = _blank_null_sentinels(sdf)
        sdf = _apply_control_defaults(sdf, suppressed=suppressed, effective=eff,
                                      explicitly_mapped=explicit)
        sdf = _strategy_frame_rules(sdf, obj_name)
        out[str(s.sheet_name or "")] = sdf
    return out, df


# Coded-value (LOV) enforcement lives in lov_service alongside the code that mines
# the accepted values out of the template descriptions. Re-exported here because
# this is where it's applied.
from app.services.lov_service import enforce_coded_values  # noqa: E402


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column headers to UPPER_UNDERSCORE (Oracle FBDI format)."""
    df.columns = [c.strip().upper().replace(" ", "_").replace("-", "_") for c in df.columns]
    return df


# Input date spellings we accept, in priority order. Ambiguous DD/MM vs MM/DD is
# resolved US-first (%m/%d/%Y) to match the existing behaviour and the US-sourced
# extracts in play; a value that only parses as DD/MM still parses on the next pass.
#
# The two timestamp forms with a DASH date were missing, which is how the most
# common database spelling of all — "2020-01-15 00:00:00", what every SQL/ODBC
# export writes — reached the loader unconverted. "15-JAN-2020" is Oracle's own
# default DATE display, so EBS/SQL*Plus extracts carry it constantly.
_DATE_INPUT_FORMATS = (
    "%Y%m%d",
    "%Y-%m-%d", "%Y/%m/%d",
    "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%m/%d/%Y", "%m/%d/%Y %H:%M:%S", "%m-%d-%Y",
    "%d/%m/%Y", "%d-%m-%Y",
    "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%b %d, %Y",
)


# The date spelling every output carries. Analyst, 05-Aug: "all dates should be
# yyyy/mm/dd format" — chosen for FBDI and the Excel templates alike (HDL already
# wrote it). This is the ONE place the output mask is defined; everything below
# formats through it so the CSV, the workbook and the DATE rules cannot drift to
# three different spellings.
FBDI_DATE_FORMAT = "%Y/%m/%d"

# Tokens that mean "today", not a literal string. An analyst who sets a date
# column's constant to SYSDATE means Oracle's current date, not the seven letters
# "SYSDATE" — which is what shipped: the BOM Effective Date column carried the text
# SYSDATE on every row. Resolved to today in the output spelling.
_TODAY_TOKENS = {"sysdate", "today", "now", "current_date", "currentdate",
                 "system date", "systemdate", "getdate()", "current date"}


def to_fbdi_date(v: Any) -> Any:
    """One cell → ``yyyy/mm/dd``, or the value untouched if it is not a date.

    Untouched is deliberate: a column that turns out to hold free text must not be
    mangled, and an unparseable date is more useful in the reject report as the
    analyst's original string than as a blank. The exception is a SYSDATE-style
    token, which is an INSTRUCTION ("use today"), not free text, and is resolved.
    """
    if v is None or str(v).strip() == "":
        return v
    s = str(v).strip()
    if s.lower() in _TODAY_TOKENS:
        return datetime.utcnow().strftime(FBDI_DATE_FORMAT)
    # Fractional seconds ("2020-01-15 00:00:00.000") — strptime has no optional
    # group for them, so drop the fraction before matching.
    core = re.sub(r"\.\d+$", "", s)
    for fmt_in in _DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(core, fmt_in).strftime(FBDI_DATE_FORMAT)
        except ValueError:
            pass
    return v


def _format_date_columns(df: pd.DataFrame, fields: list) -> pd.DataFrame:
    """Reformat any date/Date columns to ``yyyy/mm/dd`` (see FBDI_DATE_FORMAT).

    Matched on a NORMALISED name (case and punctuation folded), because the frame's
    headers and the template's field names routinely disagree on both: the EBS path
    runs ``_normalize_columns`` first, so ``EffectiveStartDate`` arrives as
    ``EFFECTIVE_START_DATE``. The previous exact ``col in date_field_names`` test
    therefore matched nothing on that path and shipped ``2020-01-15`` unconverted —
    every dated row a mismatch. Found by tests/test_ebs_output.py, which encoded
    the intent from the start; the implementation never met it.
    """
    date_field_names = {
        re.sub(r"[^a-z0-9]", "", (f.field_name or "").lower())
        for f in fields
        if (f.data_type or "").lower() in ("date", "datetime")
    }
    date_field_names.discard("")
    for col in df.columns:
        if re.sub(r"[^a-z0-9]", "", str(col).lower()) in date_field_names:
            df[col] = df[col].apply(to_fbdi_date)
    return df


# Sentinel strings that legacy/SQL exports (SyteLine, NetSuite saved searches,
# etc.) write for "no value". Loaded verbatim into Oracle they'd become the
# literal text "NULL"/"N/A" instead of an empty cell, so blank them at generate.
_NULL_SENTINELS = {"null", "(null)", "#n/a", "n/a", "nan", "none", "\\n"}


def _blank_null_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    """Replace whole-cell null sentinels (case-insensitive) with empty strings.
    Whole-cell match only, so a real value like a description containing the word
    is never touched."""
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        mask = s.astype(str).str.strip().str.lower().isin(_NULL_SENTINELS)
        if mask.any():
            df.loc[mask, col] = ""
    return df


def _resolve_today_tokens(df: pd.DataFrame) -> pd.DataFrame:
    """Whole-cell SYSDATE/TODAY/NOW → today's date, in EVERY column, type-blind.

    ``to_fbdi_date`` already resolves these tokens, but only on columns
    ``_format_date_columns`` recognises as date-typed. A template that types a date
    field as *Character* slips past that filter: the BOM ``Effective Date`` column is
    declared Character, its default is the literal ``SYSDATE``, and it is filled by
    ``_apply_control_defaults`` — AFTER the date pass — so every one of the 5,000+
    rows shipped the seven letters "SYSDATE" instead of a date, which Oracle rejects.

    A cell equal to one of these tokens is an INSTRUCTION ("use today"), never valid
    output, whatever the column's declared type — so this is the type-independent
    backstop, run on the finished frame after every default/decision has landed.
    Whole-cell, token-set match only (same shape and safety as
    ``_blank_null_sentinels``), so a real value that merely contains the word is
    untouched.
    """
    today = datetime.utcnow().strftime(FBDI_DATE_FORMAT)
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        mask = s.astype(str).str.strip().str.lower().isin(_TODAY_TOKENS)
        if mask.any():
            df.loc[mask, col] = today
    return df


def _dedup(cols: list[str]) -> list[str]:
    seen: set[str] = set()
    return [c for c in cols if not (c in seen or seen.add(c))]


# Supplier email masking: on a test / migration supplier load, real e-mail
# addresses in the file can make Oracle fire supplier/contact notifications. We
# neutralise them by prefixing "xx" (so "ap@x.com" -> "xxap@x.com", an invalid
# address that won't route). Applied to any email-named column of a Supplier
# object at generate. Idempotent (won't double-prefix) and skips blanks.
_SUPPLIER_EMAIL_PREFIX = "xx"


def _mask_supplier_emails(df: pd.DataFrame, prefix: str = _SUPPLIER_EMAIL_PREFIX) -> pd.DataFrame:
    for col in df.columns:
        key = re.sub(r"[^a-z0-9]", "", str(col).lower())
        if "email" not in key:
            continue
        s = df[col].astype(str)
        mask = s.str.strip().ne("") & ~s.str.strip().str.lower().isin(_NULL_SENTINELS) \
            & ~s.str.startswith(prefix)
        if mask.any():
            df.loc[mask, col] = prefix + s[mask]
    return df


def _safe_sheet_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "").strip()).strip("_") or "sheet"


# Supplier FBDI layout + Oracle file naming live in a pure, dependency-light
# module so they can be unit tested without the Beanie/Mongo stack. The generator
# just delegates to them (reorder to the analyst tab sequence + END terminator;
# zip/CSV names per the Tejaswi spec).
from app.services.supplier_fbdi_layout import (  # noqa: E402
    apply_supplier_layout as _supplier_layout,
    apply_customer_layout as _customer_layout,
    norm_hdr as _norm_hdr,
    customer_csv_name_for as _customer_csv_name,
    customer_load_sequence as _customer_sequence,
    customer_in_load_scope as _customer_in_scope,
    csv_name_for as _csv_name_for,
    zip_name_for as _zip_name_for,
    apply_bom_layout as _bom_layout,
    bom_csv_name_for as _bom_csv_name,
    is_bom_sheet as _is_bom_sheet,
)


# Control-field defaults + auto-numbered keys so generated files aren't blank in
# the columns Fusion requires — mirrors how a consultant fills the template
# (Import Action = CREATE, a batch id, a running supplier number, standard
# org/type values). Applied ONLY to columns the source left entirely blank.
_CONTROL_DEFAULTS: dict[str, str] = {
    # Applied by EXACT column name to blank columns only, so these are safe to
    # merge across objects (a supplier sheet has no "Transaction Type" column,
    # an item sheet has no "Supplier Type", etc.). Keys are lower-case with any
    # trailing "*" already stripped (matcher strips "*").
    # --- Supplier import (POZ_SUPPLIERS_INT) — analyst-confirmed lookup codes.
    # Oracle expects the internal code casing here, not the display label:
    # Tax Org Type = CORPORATION, Supplier Type = SUPPLIER, Business
    # Relationship = SPEND_AUTHORIZED (so sites/POs can transact immediately).
    "import action": "CREATE",
    "batch id": "900001",
    "tax organization type": "CORPORATION",
    "organization type": "CORPORATION",
    "supplier type": "SUPPLIER",
    "business relationship": "SPEND_AUTHORIZED",
    "federal reportable": "N",
    # NOTE: "delivery channel" is intentionally NOT a control default — it is
    # DERIVED per-row from the Email/Fax Transaction flags (CASE_WHEN: Email=Yes ->
    # EMAIL, Fax=Yes -> FAX, else blank). A blanket constant here was forcing every
    # row to EMAIL and overwriting the derivation.
    # --- Supplier address (POZ_SUPPLIER_ADDRESSES_INT) ---
    # "address name": "PRIMARY" — REMOVED. The signed strategy says the opposite in
    # so many words (supplier_strategy_defaults.json: "Address Name is the City Name
    # (e.g. Austin). NOT the constant 'PRIMARY'."), and the analyst reported it
    # twice: "Address name is shown mapped correctly to city in the UI, but the
    # generated file contains (static value PRIMARY)".
    #
    # Removing it from _AUTHORITATIVE was not enough, because the plain
    # _CONTROL_DEFAULTS branch fills any column that reaches finalize entirely blank
    # and has NO explicitly_mapped guard at all. So whenever the city did not
    # materialise in the frame — a renamed column, a sheet-routing miss, a mapping
    # row that lost the dedup — PRIMARY was written on every row, and the screen
    # (which reads the mapping) and the file (which reads the frame) disagreed.
    #
    # A control default is for a column nobody has an opinion about. This one has a
    # signed opinion, so it does not belong in the table in either form: an
    # unpopulated Address Name must ship EMPTY and be visible as a gap, not be
    # papered over with a value the strategy forbids.
    "pay": "Y",
    "ordering": "Y",
    "rfq or bidding": "Y",
    # --- Supplier site (POZ_SUPPLIER_SITES_INT) ---
    "supplier site": "PRIMARY",
    # --- Supplier site assignment (POZ_SITE_ASSIGNMENTS_INT) ---
    # Business-unit columns are client-specific; default to the primary BU for
    # this engagement so the file is load-ready (override per-row via mapping).
    "client bu": "Nextpower LLC Business Unit",
    "procurement bu": "Nextpower LLC Business Unit",
    "bill-to bu": "Nextpower LLC Business Unit",
    # --- Supplier contacts (POZ_SUP_CONTACTS) ---
    "administrative contact": "Y",
    # "user account action" removed — it is VALUE-MAPPED per row from Login Access
    # (Yes -> CREATE_USER_ACCOUNT, No -> blank). A blanket constant "NONE" here was
    # forcing every row to NONE (authoritative overwrite) so "No" rendered as NONE.
    # --- Item (Product Hub) — sensible defaults; confirm vs a reference ---
    "transaction type": "SYNC",
    "item class": "Root Item Class",
    # --- Customer (Trading Community / AR) — confirm vs a reference ---
    "insert update flag": "I",
    "create or update record": "1",
}


# `_by_a_person` used to live here — "is the approver a human, or the learning
# engine?" — and gated both `analyst_default` and `analyst_keeps_blank`. Both gates
# were removed on 05-Aug after each was caught producing a file that contradicted
# its own mapping screen, so nothing reads it and it is deleted rather than left
# sitting available to be reached for again.
#
# The distinction it drew is not meaningless; it is just not PRECEDENCE. Provenance
# is still recorded on every row and still shown. What decides is the date.


def analyst_default(m) -> str | None:
    """The decided default value on this mapping row, or None.

    Module-level and pure so the rule can be tested directly. It decides whether a
    constant reaches the file, whether an otherwise-empty interface sheet is written
    with rows at all, and whether the generated linkage may overwrite the column —
    three separate 31-Jul issues turn on it.

    THE AUTHORSHIP GATE IS GONE. It used to end `if _by_a_person(m) else None`, and
    on 05-Aug that shipped a Customer file contradicting its own mapping screen:

        Party Original System                     screen NETSUITE   file LEGACY
        Customer Account Source System Reference   screen LEGACY_SYSTEM
                                                            file NXT000001_C1
        Account Site Source System                 screen NETSUITE   file LEGACY

    All three rows read approved, dated 05-Aug, `approved_by="learning-engine"`.
    Not a person -> None -> the field never entered the protected set -> the
    linkage glue in `customer_structure_service.apply_to_frame` overwrote it. The
    other twelve fixed values on the same file landed correctly, purely because
    the glue does not touch those columns.

    This is the FIFTH place the same rule was wrong, and the same fix as
    `_decision_outranks_directive` two hundred lines up:

        "Sources are all equal: mapping workbook, gold standard, learning, steer
         box, grid edit, custom rule. Newest wins. Authorship is provenance, not
         precedence."                                        -- ONE_DATED_STORE.md

    The old docstring's fear -- "a seeded row re-populates the fields the analyst
    has been clearing" -- was answered by the one-row store, not by authorship. A
    key holds ONE row now. There is no older seeded statement to lose to, and if a
    newer one exists it is the client's newest statement and SHOULD win.

    What a row still has to do to count: carry a non-empty value, and be approved
    or overridden. Those are unchanged.
    """
    dv = getattr(m, "default_value", None)
    if dv is None or not str(dv).strip():
        return None
    if (getattr(m, "status", "") or "") not in ("approved", "overridden"):
        return None
    return str(dv)


def analyst_keeps_blank(m) -> bool:
    """Keep blank: not_applicable with the default cleared.

    not_applicable WITH a default means "populate with this constant" (Invoice Match
    Option = Receipt), which is the opposite instruction — so the cleared default is
    load-bearing, not incidental.

    THE AUTHORSHIP GATE IS GONE HERE TOO, and leaving it out of the morning's fix
    was a mistake. It was judged inert on the grounds that the learning engine does
    not set `not_applicable`. It does — by propagating one, which is the only way a
    Keep blank pressed once reaches the other eighteen sheets that carry the same
    field name.

    Measured on Customer 03082026, 05-Aug:

        Batch Identifier   HZ_IMP_PARTIES_T       approved        by a person
        Batch Identifier   18 other interfaces    not_applicable  by learning-engine

    Eighteen Keep blanks, and CONV-E3F9D5 in the shipped file on every one of them,
    because `_by_a_person` said the engine's copy of the instruction was not the
    instruction. Tejaswini reported this exact symptom on 31-Jul — "Batch Identifier
    came back after Keep blank" — and it was half-fixed then: the glue learned to
    respect a decision, but this function would not call a propagated one a
    decision.

    Same rule as `analyst_default`, for the same reason. Newest wins; authorship is
    provenance. What still has to be true is unchanged: status `not_applicable`,
    and the default genuinely cleared.
    """
    if (getattr(m, "status", "") or "") != "not_applicable":
        return False
    dv = getattr(m, "default_value", None)
    if dv is not None and str(dv).strip():
        return False
    return True


def _header_label(f) -> str:
    """The header text to write for a field. Prefer the raw header captured at
    parse time (which carries Oracle's exact '*' required markers, e.g.
    'Import Action *', 'Supplier Name*'); fall back to appending a trailing '*'
    for required fields when only the cleaned name is stored (older templates)."""
    raw = (getattr(f, "display_name", None) or "").strip()
    if "*" in raw:
        return raw
    base = (f.field_name or "").strip()
    if getattr(f, "required", False) and base and "*" not in base:
        return base + " *"
    return base or raw
_SEQ_FIELDS: set[str] = {
    "suppliernumber", "supplierpartynumber",   # supplier
    "partynumber", "customeraccountnumber", "customernumber",  # customer
}

# Sequence fields where a REAL source value must win over the running number.
# The analyst confirmed the supplier's legacy "Number" is the number of record,
# so when the source maps one in we keep it and only auto-number the blanks. The
# customer party/account keys stay authoritative sequences because the 19-sheet
# linkage glue references that generated running number.
_SEQ_PREFER_SOURCE: set[str] = {"suppliernumber", "supplierpartynumber"}

# Control fields that are CONSTANTS in the Oracle gold templates. These are set
# AUTHORITATIVELY — the standard value is written even if auto-map filled the
# column with a (usually wrong) source guess, e.g. Address Name / Supplier Site
# landing a street address or "No", or User Account Action getting a phone
# fragment. Excludes fields whose value is genuinely per-row (Business Unit,
# currency) or correctly source-mapped (names, addresses, tax ids, web site).
_AUTHORITATIVE: set[str] = {
    "import action", "batch id",
    "federal reportable",
    # "delivery channel" removed — it is per-row derived (Email/Fax -> EMAIL/FAX/
    # blank), not a forced constant.
    # "pay" and "ordering" stay authoritative — strategy 7.2 sets both to Y for
    # ALL addresses. "rfq or bidding" removed: the analyst rule is "blank if not
    # mapped", and there is no strategy default for it, so force-writing a control
    # constant over every row contradicted the requirement (same defect class as
    # Address Name / QA #8). It remains in _CONTROL_DEFAULTS only as a fill for a
    # wholly empty column, and the seeded suppress_field learning blanks it.
    "pay", "ordering",
    # "address name" and "supplier site" REMOVED from the authoritative set. The
    # signed NextPower Supplier Conversion Strategy (v1.0, section 7.2/7.3) states
    # Address Name = City Name (e.g. "Austin") and Supplier Site = BU + City
    # (e.g. "US-Austin"). Forcing the constant "PRIMARY" over a mapped city was
    # QA issue #8 — the UI showed the correct mapping and the generated file
    # contained PRIMARY. They stay in _CONTROL_DEFAULTS as a last-resort fill for
    # a column the source left completely empty, but they no longer overwrite.
    # "user account action" removed — per-row VALUE_MAP from Login Access must win
    # (No -> blank, not the forced constant "NONE").
}
# Tax Organization Type, Supplier Type and Business Relationship were previously
# forced constants. Per the analyst supplier mapping doc they are value-MAPPED from
# source (Entity Type, Category, Vendor Approval Status → e.g. Approved =
# SPEND_AUTHORIZED). So they are NO LONGER authoritative: when the source maps them
# (with a crosswalk) that value wins; the _CONTROL_DEFAULTS constant now only fills
# the column when the source left it entirely blank (a safe fallback).


def _apply_control_defaults(df: pd.DataFrame, seq_start: int = 100000,
                            suppressed: set | None = None,
                            effective: dict | None = None,
                            explicitly_mapped: set | None = None) -> pd.DataFrame:
    """``explicitly_mapped``: normalized field names the analyst deliberately bound
    to a real source column (curated seed, approved or overridden mapping). Those
    are never overwritten by an authoritative constant — QA issue #8, where the
    seeded eBOS ``Address Name <- city`` mapping was shown correctly in the UI but
    the generated file contained the forced constant ``PRIMARY``. Mirrors
    ``defaults_service._effective`` which already skips fields with a source column.
    """
    n = len(df)
    if n == 0:
        return df
    suppressed = suppressed or set()
    effective = effective or {}
    explicitly_mapped = explicitly_mapped or set()
    for col in df.columns:
        key = str(col).strip().lower().rstrip("*").strip()
        # The user's gold example / prompt marked this field as intentionally
        # blank — never fill it with a control default or authoritative constant.
        if key in suppressed:
            continue
        keyc = key.replace(" ", "")
        if keyc in _SEQ_FIELDS:
            # NEVER invent a key. Keep whatever the source mapped; leave the rest
            # BLANK so Fusion assigns its own on the CREATE load.
            #
            # These columns used to be back-filled with a running 100000, 100001…
            # sequence whenever the source left them empty. Three things went wrong:
            #   * the NetSuite extract leaves Supplier Number empty on every row, so
            #     EVERY supplier got a fabricated number, which would have become its
            #     permanent Fusion supplier number with no link to the legacy system;
            #   * these are the de-dup business keys, so handing every row a distinct
            #     value made genuine duplicates look unique and the golden-record
            #     collapse could never fire;
            #   * reviewers saw five "3X Motion Technologies" rows numbered
            #     100005-100009 and reasonably read them as five different suppliers.
            # Analyst 28-Jul: "due to that number we have so many duplicate fields,
            # please do not generate it if its not mapped" and "same for other auto
            # generated fields as well, if there is no input in the tool or no
            # mapping, do not generate auto number."
            cur = df[col].astype(str).str.strip()
            _blanks = {"", "nan", "none", "null", "na", "<na>"}
            df[col] = ["" if cur.iat[i].lower() in _blanks else cur.iat[i]
                       for i in range(n)]
        elif key in _AUTHORITATIVE and key not in explicitly_mapped:
            # Gold-constant control field — write the standard value, overriding a
            # wrong AUTO-MAPPED source guess. Skipped when the analyst explicitly
            # bound this field to a source column (issue #8): a deliberate mapping
            # outranks the constant, and silently discarding it made the UI and the
            # generated file disagree.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in _AUTHORITATIVE and bool((df[col].astype(str).str.strip() == "").all()):
            # Explicitly mapped but the source produced nothing at all — fall back
            # to the standard constant rather than shipping an empty control column.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in _CONTROL_DEFAULTS and bool((df[col].astype(str).str.strip() == "").all()):
            # Other defaults only fill columns the source left entirely blank.
            df[col] = _CONTROL_DEFAULTS[key]
        elif key in effective and bool((df[col].astype(str).str.strip() == "").all()):
            # Effective defaults from defaults_service (curated control + learned
            # example defaults) — the SAME layer the Mapping Review UI and the
            # mapping export show. Fills blank, non-suppressed columns so the output
            # FBDI matches what the analyst sees (Issue #2: e.g. Include in Credit
            # Check / Credit Hold / Send Dunning Letters / Send Statement).
            df[col] = effective[key]
    return df


def _strategy_sheets_to_drop() -> set:
    """Interface sheets that must not appear in a generated workbook at all.

    Never blocks generation: a strategy file that cannot be read costs the drop,
    not the output.
    """
    try:
        from app.services.strategy_overlay import sheets_to_drop
        return sheets_to_drop()
    except Exception:                                           # noqa: BLE001
        log.exception("could not read the drop-sheet list")
        return set()


async def generate_output_artifact(conversion: Conversion, fmt: str = "csv",
                                   include_header: bool | None = None,
                                   merged_df: "pd.DataFrame | None" = None) -> ConvertedOutput:
    """``include_header``: None = auto, decided by FORMAT rather than by object —
    the filled Oracle Excel templates keep their column-label row, and CSV/zip
    output is headerless because that is what the FBDI loader expects (a header
    line is read as data and rejects the first record). True/False = the user's
    explicit toggle, forcing headers on/off for every sheet of this output.

    ``merged_df``: when provided, this ALREADY-BUILT frame is written instead of
    converting ``conversion``'s own source — used by the merge-by-interface path,
    where several per-source conversions are each converted and then merged into one
    frame that is finalized/written once (one file per interface)."""
    from app.models.fbdi import FBDISheet

    # HDL divert: HCM objects (Employee HDL) are not FBDI — they load as
    # pipe-delimited .dat files, so they go to the dedicated HDL generator instead
    # of the CSV/XLSX fan-out below. Detected by the template's business object.
    _tpl_for_route = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    try:
        from app.services.hdl_output_service import is_hdl_conversion, generate_hdl_artifact
    except ImportError:
        is_hdl_conversion = None  # type: ignore
    # Scope the import guard to the import only — a real error inside the HDL
    # generator must surface, not silently fall through to the FBDI path.
    if is_hdl_conversion and is_hdl_conversion(_tpl_for_route, conversion):
        # fmt THREADED THROUGH. It was dropped here, so "HDL Template" and "DAT
        # files" both returned the .dat bundle — the divert knew the conversion was
        # HDL and forgot what the analyst had asked for.
        return await generate_hdl_artifact(conversion, fmt=fmt)

    # How many target fields this object has — used to gate the two DB-heavy passes
    # below. A 19-sheet Customer/Item load has ~1200 fields; re-applying every
    # learned rule and re-capturing learnings across all of them on each generate is
    # hundreds of Mongo round-trips that made the request HANG on a small instance.
    _field_count = await FBDIField.find(
        FBDIField.template_id == conversion.template_id
    ).count() if conversion.template_id else 0
    _heavy = _field_count > 300

    # RESOLVE THROUGH THE ONE DATED STORE before building the output, so the file
    # reflects the latest decision about every field rather than whatever happened
    # to be copied onto these rows earlier.
    #
    # This used to be skipped for heavy multi-sheet objects, on the grounds that
    # their rows already carried the library and re-applying it was what made
    # generation hang. Both halves of that have gone: the rows are a VIEW, so
    # "they already carry it" is only true until someone says something newer, and
    # the pass now reads the store once and writes only the rows that actually
    # change — so a 19-sheet Customer load costs one query rather than hundreds.
    # Skipping it meant the biggest objects were the ones most likely to ship
    # against a stale copy, which is the wrong way round.
    _learning_error: str | None = None
    _applied_learnings = 0
    try:
        from app.services.learning_service import apply_learned_to_conversion
        _pre_maps = await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion.id
        ).to_list()
        _applied_learnings = await apply_learned_to_conversion(
            conversion, _pre_maps, force=True)
    except Exception as _al_exc:  # noqa: BLE001
        # Never block generation on the learning pass — but never hide it either.
        # A bare `pass` here meant that if applying the library threw, the file was
        # generated with NO learnings applied and nothing anywhere said so: the
        # analyst's approved mappings simply did not reach the output, which reads
        # exactly like "approvals are not being saved". Same shape as the
        # required-field section that silently reported zero for weeks.
        _learning_error = f"{type(_al_exc).__name__}: {_al_exc}"[:300]
        log.exception("apply_learned_to_conversion failed for conversion %s — the "
                      "output was generated WITHOUT the learning library",
                      conversion.id)
    # Per-source frames, kept UNMERGED so each Oracle interface sheet can be fed by
    # the source sheet that actually supplies it. Only populated when the conversion
    # is bound to more than one source (e.g. a Customer + Address workbook).
    _src_frames: dict = {}
    if merged_df is not None:
        df = merged_df
    else:
        df = (await build_converted_dataframe(conversion, collect_frames=_src_frames))[0]
    if len(_src_frames) < 2:
        _src_frames = {}          # single source — nothing to route
    # Multi-source Customer merge: the frame carries a per-row grain tag and the
    # customer key (see customer_merge). When present, each interface sheet is given
    # only its own grain's rows and is linked by entityid — set up here, consumed by
    # `_frame_for` and the linkage glue below. Any other object leaves this off and
    # keeps the merged-frame behaviour unchanged.
    from app.services import customer_merge as _cm
    _grain_merge = _cm.GRAIN_COL in getattr(df, "columns", [])
    _sheet_ref_holder: dict = {"ref": None}   # this sheet's entityid linkage refs
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None

    # What this object is called. Prefer the template's business object (the
    # precise interface, e.g. "Supplier Address"), then the conversion's target
    # object (always set), then the template name — "fbdi" only when nothing is
    # available.
    #
    # ASSIGNED HERE, ONCE, AS SOON AS ``template`` EXISTS.
    #
    # It used to be assigned about sixty lines further down, after the DQ pass —
    # and the DQ pass's own timing log referenced it. Because a name assigned
    # anywhere in a function is local for the whole of it, that read raised
    # UnboundLocalError before a single byte was written, on EVERY call: 04-Aug
    # 15:51 through 05-Aug, no output of any format for any object. The log line
    # that broke it is advisory, which is the part worth remembering — the
    # failure was total and the code that caused it did nothing.
    obj_name = (
        (template.business_object if template else None)
        or getattr(conversion, "target_object", None)
        or (template.name if template else None)
        or "fbdi"
    )

    # Fetch fields (interface sequence) + sheets so we can emit exactly the
    # template's columns and, for multi-sheet workbooks, one file per sheet.
    fields = await FBDIField.find(
        FBDIField.template_id == template.id
    ).sort(+FBDIField.sequence).to_list() if template else []
    sheets = await FBDISheet.find(
        FBDISheet.template_id == template.id
    ).sort(+FBDISheet.sequence).to_list() if template else []

    # --- Generate-time data quality on the MERGED frame: cleanse + validate ---
    # Cleansing (universal trim + custom cleansing rules) is applied to df so the
    # written file carries the cleaned values; validation (built-in FBDI checks +
    # custom validation rules) produces an advisory issues report attached to the
    # artifact. Runs off the event loop; never blocks file generation.
    dq_report = None
    _dq_t0 = _time.monotonic()
    try:
        from app.services.generate_dq import apply_cleansing, validate_frame, build_report
        from app.services.dq_rule_service import load_rules
        from app.services.client_service import client_id_for_conversion
        _dq_obj = (template.business_object if template else None) or (conversion.target_object or "")
        _cid = await client_id_for_conversion(conversion)
        _cleanse_rules = await load_rules(_dq_obj, _cid, "cleansing")
        _val_rules = await load_rules(_dq_obj, _cid, "validation")
        df, _dq_fixes = await asyncio.to_thread(
            apply_cleansing, df, _cleanse_rules,
            getattr(conversion, "cleansing_profile", None))
        # Everything the validator can check with. allowed_values, precision, scale
        # and do_not_populate were all absent here, so the value-set check never
        # fired at generation even for columns where Oracle publishes the codes —
        # the engine had the check and this call site starved it of the metadata.
        _tf = [{"field_name": f.field_name, "required": bool(f.required),
                "data_type": f.data_type, "max_length": f.max_length,
                "format_mask": f.format_mask,
                "allowed_values": getattr(f, "allowed_values", None) or [],
                "precision": getattr(f, "precision", None),
                "scale": getattr(f, "scale", None),
                "do_not_populate": bool(getattr(f, "do_not_populate", False)),
                "db_column": getattr(f, "db_column", None)} for f in fields]
        _dq_issues = await asyncio.to_thread(validate_frame, df, _tf, _val_rules, 2000)
        dq_report = build_report(_dq_issues, _dq_fixes)
        if _learning_error:
            # The output does NOT carry the learning library. Recorded on the
            # artifact so the reason travels with the file rather than living
            # only in a server log nobody reads.
            dq_report["learning_error"] = _learning_error
        dq_report["learnings_applied"] = _applied_learnings
    except Exception as _dq_exc:  # noqa: BLE001 — DQ is advisory; never block generation
        import logging as _lg
        _lg.getLogger(__name__).exception("generate DQ step failed")
        # Surface the reason (diagnostic) instead of silently dropping the report.
        dq_report = {"error_count": 0, "warning_count": 0, "hard_error_count": 0,
                     "blocked": False, "cleansing_fix_count": 0, "cleansing_fixes": [],
                     "top_issues": [], "dq_error": f"{type(_dq_exc).__name__}: {_dq_exc}"[:300]}

    log.info("generate phase — %s: cleanse + validate took %.1fs",
             obj_name, _time.monotonic() - _dq_t0)

    # Group fields by their interface sheet (preserving field sequence).
    fields_by_sheet: dict[Any, list] = {}
    for f in fields:
        fields_by_sheet.setdefault(f.sheet_id, []).append(f)
    sheets_with_fields = [s for s in sheets if s.id in fields_by_sheet]

    # Fields the user's gold example / prompt marked as intentionally blank
    # (a not_applicable mapping wins the dedup). These must stay empty in the
    # output — no control default, no authoritative constant.
    _all_maps = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == conversion.id
    ).to_list()
    _best_m = best_mapping_by_target(_all_maps)
    _fbyid = {f.id: f for f in fields}
    suppressed_keys = {
        _fbyid[tid].field_name.strip().lower().rstrip("*").strip()
        for tid, _m in _best_m.items()
        if _m.status == "not_applicable" and tid in _fbyid and _fbyid[tid].field_name
        # An explicit default_value on the field is intent to POPULATE it (e.g. an
        # analyst set Invoice Match Option = "Receipt"), so it must not be suppressed
        # even though the mapping is not_applicable (no source column).
        and not (getattr(_m, "default_value", None) and str(_m.default_value).strip())
    }
    # Fields the analyst deliberately bound to a source column. An authoritative
    # control constant must NOT overwrite these (issue #8: eBOS Address Name was
    # mapped to `city` in the UI but the file shipped the forced "PRIMARY").
    # "suggested" is excluded on purpose — that is auto-map guessing, which is
    # exactly what the authoritative constants exist to correct.
    _MAPPED_OK = {"approved", "overridden"}
    explicitly_mapped_keys = {
        _fbyid[tid].field_name.strip().lower().rstrip("*").strip()
        for tid, _m in _best_m.items()
        if tid in _fbyid and _fbyid[tid].field_name
        and (_m.source_column or "").strip()
        and (_m.status or "") in _MAPPED_OK
    }

    # Effective defaults (curated control + learned example defaults) — the SAME
    # layer the Mapping Review UI and the mapping export display. Applied to blank,
    # non-suppressed columns at finalize so the output FBDI matches what the analyst
    # sees (Issue #2). use_ai=False keeps generation off the model path (fast); the
    # reported fields are curated/control defaults, so they're covered.
    _eff_defaults: dict = {}
    _eff_scopes: dict = {}
    try:
        from app.services.defaults_service import compute_effective_defaults
        _eff = await compute_effective_defaults(conversion, use_ai=False)
        _eff_defaults = _eff.get("defaults", {}) or {}
        # Which sheets each default may touch. Without this a default captured on
        # one sheet reached every sheet with a column of the same name.
        _eff_scopes = _eff.get("scopes", {}) or {}
    except Exception:  # noqa: BLE001 — defaults are best-effort; never block generation
        _eff_defaults = {}
        _eff_scopes = {}

    fmt = fmt.lower()
    out_dir = settings.output_path / f"conversion_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # obj_name is resolved once, up where ``template`` is loaded. Recomputing it
    # here is what let the two drift apart in the first place.

    # Fields the signed strategy / analyst rules say must ship BLANK. Merged into
    # the suppression set so neither the auto-number sequence nor the "fill an empty
    # column" control default can resurrect them (both did, in the 28-Jul run).
    _strategy_blanks = _strategy_blank_fields(obj_name)

    # "template" output = the REAL Oracle FBDI workbook, filled in. Materialize the
    # bundled source file (disk, or rehydrated from Mongo after a redeploy). If the
    # template has no stored file we can't fill it, so degrade gracefully to a fresh
    # xlsx workbook rather than failing the whole generation.
    _template_src_path: str | None = None
    _template_fallback = False
    if fmt == "template":
        if template:
            try:
                from app.services.fbdi_service import materialize_template_file
                _p = await materialize_template_file(template)
                _template_src_path = str(_p) if _p else None
            except Exception:  # noqa: BLE001
                _template_src_path = None
        if not _template_src_path:
            # SILENT DEGRADATION WAS THE BUG: falling back to "xlsx" builds a fresh
            # workbook whose columns are the MAPPED set (i.e. the CSV column
            # structure), not the Oracle template's own layout — and the user was
            # given no indication that "Filled Excel templates" had not actually
            # used the template. Still degrade rather than fail the whole run, but
            # make it loud so the cause (no stored template file) is diagnosable.
            log.warning(
                "template fill unavailable for conversion %s (object=%r, template=%r): "
                "no stored FBDI workbook to populate — falling back to a generated "
                "xlsx whose columns follow the mapped/CSV structure, NOT the original "
                "Oracle template layout. Re-upload the FBDI template for this object.",
                conversion.id, obj_name, getattr(template, "name", None),
            )
            _template_fallback = True
            fmt = "xlsx"  # no source workbook to populate — fall back

    # ── Per-interface-sheet source routing ──────────────────────────────────
    # One conversion, one FBDI bundle, even when the input spreads the object
    # across several worksheets. A merged frame cannot express this: a Customer
    # workbook has 5,489 party rows and 22,505 address rows, so the party sheets
    # and the address sheets need DIFFERENT row grains. For each interface sheet
    # we pick the bound source that supplies the most of that sheet's mapped
    # source columns; ties and no-evidence cases fall back to the merged frame,
    # which is exactly the previous behaviour.
    _src_by_field: dict = {}
    for _m in _all_maps:
        if _m.source_column:
            _src_by_field.setdefault(_m.target_field_id, str(_m.source_column))

    def _frame_for(sfields: list) -> pd.DataFrame:
        # Multi-source Customer merge: give this sheet only its own grain's rows
        # (party/account sheets deduped to one row per customer), and remember the
        # rows' entityid so the linkage glue points every child at its customer's
        # party. Falls back to the full frame for a sheet whose grain is unknown or
        # absent (customer_merge.sheet_rows), so no sheet is emptied on a guess.
        if _grain_merge:
            sub = _cm.sheet_rows(df, _sheet_name_of(sfields))
            _sheet_ref_holder["ref"] = _cm.sheet_reference(sub)
            return sub
        wanted = {_src_by_field.get(f.id) for f in sfields}
        wanted.discard(None)
        return route_frame(wanted, _src_frames, df)

    # ── Per-sheet analyst decisions ─────────────────────────────────────────
    # The transformed frame is keyed by FIELD NAME (`out_cols[tgt.field_name]`), so
    # every interface sheet reads the same single column per name. Oracle repeats
    # field names across sheets constantly, which makes a per-sheet decision
    # impossible to express in that frame: one sheet's default is applied to all of
    # them, and where two sheets disagree the later mapping silently wins.
    #
    # Both halves were reported on 31-Jul, from opposite ends:
    #   "Role Type … set … for both target sheets, however it only reflects in the
    #    HZ_IMP_ACCTCONTACTS_T sheet and not in HZ_IMP_CONTACTROLES"   (too narrow)
    #   "Insert Update Indicator was set … as I … However it should only reflect in
    #    the RA_CUSTOMER_PROFILES_INT_ALL sheet"                        (too wide)
    #
    # MappingSuggestion rows are already per-sheet — target_field_id belongs to one
    # sheet — so the decision exists; only the frame could not carry it. Rather than
    # re-key the whole frame (which every other path depends on), each sheet's own
    # rows are re-applied to that sheet's finished frame. Last write, per sheet.
    _mbyfield: dict = {}
    for _tid, _m in _best_m.items():
        _mbyfield[_tid] = _m

    _analyst_default = analyst_default
    _analyst_keeps_blank = analyst_keeps_blank

    def _sheet_decisions(sfields: list) -> tuple[dict, set]:
        """(field_name -> constant to write, {field names the analyst decided}).

        The second half is what protects a column from the linkage glue, which used
        to overwrite Batch Identifier, Party Original System, Customer Account
        Source System and Account Site Source System unconditionally.
        """
        consts: dict = {}          # field_name -> (value, fill_blank_only)
        decided: set = set()
        _owned = (_cm.merge_owned_fields(_sheet_name_of(sfields))
                  if _grain_merge else set())
        for f in sfields:
            if f.field_name in _owned:
                # The merge authoritatively populates this field (Primary/Identifying
                # flag — REC-09/23; contact-point fields — REC-48/53/54/56/57; party
                # identity and the PROFILES forced-blanks). Neither a not_applicable
                # keep-blank nor a stray analyst constant (Contact Point Type=EMAIL on
                # every row) nor the source-system linkage glue (NETSUITE on a PROFILES
                # field the merge blanked) may override it. Marked decided REGARDLESS of
                # whether a mapping row exists — a forced-blank field the fresh project
                # never mapped has no MappingSuggestion, so this must run before the
                # `m is None` skip or the glue re-stamps it (REC-80).
                decided.add(f.field_name)
                continue
            m = _mbyfield.get(f.id)
            if m is None:
                continue
            if _analyst_keeps_blank(m):
                consts[f.field_name] = ("", False)
                decided.add(f.field_name)
                continue
            dv = _analyst_default(m)
            has_src = bool((m.source_column or "").strip())
            if dv is not None:
                # A default alongside a mapped column is a FALLBACK for the rows the
                # source leaves empty — overwriting a real mapped value with it is
                # the eBOS "Address Name shows city in the UI, ships PRIMARY" bug.
                consts[f.field_name] = (dv, has_src)
                decided.add(f.field_name)
            elif has_src and (m.status or "") in ("approved", "overridden"):
                decided.add(f.field_name)
        return consts, decided

    class _Scope:
        __slots__ = ("sheets", "exclude_sheets")

        def __init__(self, d: dict):
            self.sheets = list(d.get("sheets") or [])
            self.exclude_sheets = list(d.get("exclude_sheets") or [])

    def _eff_for_sheet(sfields: list) -> dict:
        """The effective defaults allowed on THIS sheet.

        Same ``sheet_allowed`` the matcher and the learning library use — one
        resolver, so the screen, the library and the file cannot drift apart.
        A default with no recorded scope reaches every sheet, which is the
        behaviour every existing row was captured under.
        """
        if not _eff_scopes:
            return _eff_defaults
        name = _sheet_name_of(sfields)
        try:
            from app.services.learning_service import sheet_allowed
        except Exception:  # noqa: BLE001
            return _eff_defaults
        return {k: v for k, v in _eff_defaults.items()
                if k not in _eff_scopes or sheet_allowed(_Scope(_eff_scopes[k]), name)}

    def _sheet_name_of(sfields: list) -> str | None:
        for f in sfields:
            sid = getattr(f, "sheet_id", None)
            if sid is not None and sid in _sheet_name_by_id:
                return _sheet_name_by_id[sid]
        return None

    _sheet_name_by_id = {s.id: s.sheet_name for s in sheets}

    def _apply_sheet_decisions(sdf: pd.DataFrame, sfields: list) -> pd.DataFrame:
        consts, _ = _sheet_decisions(sfields)
        if not consts or len(sdf) == 0:
            return sdf
        for fname, (val, blank_only) in consts.items():
            if fname not in sdf.columns:
                continue
            if blank_only:
                cur = sdf[fname].astype(str).str.strip()
                sdf[fname] = [val if cur.iat[i] in ("", "nan", "None") else sdf[fname].iat[i]
                              for i in range(len(sdf))]
            else:
                sdf[fname] = val
        return sdf

    def _finalize(sfields: list) -> pd.DataFrame:
        # Req 8 — exactly this sheet's interface columns, in sequence, blanks
        # where unmapped, no instruction rows. Data ops (date reformat, control
        # defaults) run while columns are still keyed by cleaned field_name; the
        # LAST step renames columns to Oracle's exact header labels (with the
        # '*' required markers) so the file matches the shipped template.
        cols = _dedup([f.field_name for f in sfields])
        sdf = _frame_for(sfields).reindex(columns=cols, fill_value="")
        # Fields the customer merge OWNS on this sheet (Primary/Identifying flag set
        # per-sheet from the customer key — REC-09/23; the CONTACTPTS fan-out fields —
        # REC-48/53/54/56/57). Their value is the merge's to decide, so it must survive
        # suppression and control defaults regardless of whether this project's mapping
        # for the field sits at not_applicable or carries a stray constant. Excluded
        # from `suppressed`, added to `explicitly_mapped`, and skipped by the keep-blank
        # and the analyst-constant in _sheet_decisions.
        _owned_keys = ({f.strip().lower().rstrip("*").strip()
                        for f in _cm.merge_owned_fields(_sheet_name_of(sfields))}
                       if _grain_merge else set())
        _supp = suppressed_keys | _strategy_blanks
        _expl = explicitly_mapped_keys
        if _owned_keys:
            _supp = _supp - _owned_keys
            _expl = explicitly_mapped_keys | _owned_keys
        # Blank legacy null sentinels BEFORE control defaults so a column the
        # source filled entirely with "NULL" is treated as empty and gets its
        # standard default, not the literal text.
        sdf = _blank_null_sentinels(sdf)
        sdf = _format_date_columns(sdf, sfields)
        sdf = _apply_control_defaults(sdf, suppressed=_supp,
                                      effective=_eff_for_sheet(sfields),
                                      explicitly_mapped=_expl)
        # THIS sheet's own analyst decisions, re-applied after the shared frame and
        # the control defaults. The frame is keyed by field name and cannot hold a
        # per-sheet decision; these rows can, and they are the analyst speaking
        # directly, so they outrank both the control table and the effective
        # defaults. See _sheet_decisions.
        sdf = _apply_sheet_decisions(sdf, sfields)
        # A SYSDATE/TODAY token is an instruction, not a literal — resolve it to
        # today AFTER every default and decision has landed, and on ALL columns, so
        # a Character-typed date field (BOM Effective Date) that slipped past the
        # date pass at _format_date_columns above still ships a real date, not the
        # word "SYSDATE".
        sdf = _resolve_today_tokens(sdf)
        # Cross-column strategy rules need the finished frame (see
        # strategy_overlay.apply_frame_rules) — e.g. blank Alternate Name where it
        # duplicates Supplier Name. Runs AFTER control defaults so a default that
        # re-creates the duplicate is caught too.
        sdf = _strategy_frame_rules(sdf, obj_name)
        # Supplier safety: neutralise e-mail columns so a migration/test load can't
        # trigger real supplier notifications. Runs while columns are still keyed by
        # field_name (before the header rename below).
        if _is_supplier:
            sdf = _mask_supplier_emails(sdf)
        hdr: dict[str, str] = {}
        for f in sfields:
            hdr.setdefault(f.field_name, _header_label(f))
        sdf.columns = [hdr.get(c, c) for c in sdf.columns]
        return sdf

    # Customer Import is a linked 19-table load: children point at parents through
    # the Source System + Source System Reference columns, which no source extract
    # provides. Generate that glue (batch id, ORIG_SYSTEM keys, -1/blank sentinels
    # per level) across the interface sheets — the same structural filling the
    # Supplier fan-out already does.
    _is_customer = ("customer" in (obj_name or "").lower()) or any(
        "hzimp" in _safe_sheet_name(s.sheet_name).lower().replace("_", "")
        for s in sheets_with_fields
    )
    # Item Import (Product Hub) is the same shape: ONE backbone interface table
    # (EGP_SYSTEM_ITEMS_INTERFACE) plus ~16 optional child tables (revisions,
    # categories, relationships, supplier, cost, EFF…) that the gold leaves blank
    # unless the source genuinely carries that content. Same over-population rule.
    _is_item = ("item" in (obj_name or "").lower()) or any(
        "egpsystemitems" in _safe_sheet_name(s.sheet_name).lower().replace("_", "")
        for s in sheets_with_fields
    )
    # Supplier object (Import / Address / Site / Contacts / Banks) — drives the
    # e-mail masking in _finalize. Matches the object name or a POZ_/supplier sheet.
    _is_supplier = ("supplier" in (obj_name or "").lower()) or any(
        _safe_sheet_name(s.sheet_name).lower().startswith("poz_")
        or "supplier" in _safe_sheet_name(s.sheet_name).lower()
        for s in sheets_with_fields
    )
    # BOM Import — the four EGP_*_INTERFACE structure tables. The sheet test comes
    # first and is the one that matters: the same object arrives as BOM, Bill of
    # Materials or Item Structure depending on who created it, and a bare substring
    # test on the name would also fire on ordinary words containing those letters.
    # The name is matched on a word boundary purely as a fallback for a conversion
    # whose sheets the spec has not heard of.
    _is_bom = any(_is_bom_sheet(s.sheet_name) for s in sheets_with_fields) or bool(
        re.search(r"\bbom\b|bills?\s+of\s+material", (obj_name or "").lower())
    )

    # Shared customer linkage config (source system code + batch), and a reference
    # series derived ONCE from the party sheet so every interface table links
    # consistently. Computed up front so we can then process sheets one at a time.
    _cust_src = str(
        getattr(conversion, "source_system_code", None)
        or getattr(conversion, "source_erp", None)
        or "LEGACY"
    ).upper().replace(" ", "_")
    _cust_batch = f"CONV-{str(conversion.id)[-6:].upper()}"

    def _cust_ref() -> list:
        if not _is_customer or not sheets_with_fields:
            return []
        try:
            from app.services.customer_structure_service import reference_series
            party = next((s for s in sheets_with_fields
                          if "parties" in _safe_sheet_name(s.sheet_name).lower()), None)
            base = _finalize(fields_by_sheet[party.id]) if party else _finalize(
                fields_by_sheet[sheets_with_fields[0].id])
            n = len(base)
            ref = reference_series(base, n, _cust_src)
            del base
            return ref
        except Exception:  # noqa: BLE001
            return []

    def _cust_apply(frame, sheet_name: str | None = None,
                    sfields: list | None = None) -> None:
        # The multi-source merge links by the CUSTOMER key: `_frame_for` left this
        # sheet's per-row entityid in the holder, and it becomes the Party/Account
        # Source System Reference so a child row points at its customer's one party
        # row. Without the merge (single source) the positional `_ref_cache` is used,
        # exactly as before.
        _ref = _sheet_ref_holder["ref"] if _grain_merge else _ref_cache
        if not _is_customer or not _ref:
            return
        try:
            from app.services.customer_structure_service import apply_to_frame
            # Columns the analyst decided on THIS sheet are off limits to the glue.
            # The glue generates the linkage no source system supplies; it is not a
            # licence to overwrite a value a person typed. Four 31-Jul issues —
            # Batch Identifier after Keep blank, and the NETSUITE defaults on Party
            # Original System / Customer Account Source System / Account Site Source
            # System — were this one line missing.
            # The glue is a FALLBACK, never an override — its own docstring says so.
            # Besides the analyst's per-sheet decisions, it must also stand off any
            # field the strategy or analyst marked BLANK / suppressed. Batch Identifier
            # is the proof: its mapping sits "approved" (empty) on the loaded sheets —
            # not "not_applicable" — so it was NOT in the per-sheet `decided` set, and
            # the glue regenerated CONV-<id> into it on every sheet despite
            # blank_fields("Customer") == {"batch identifier"}. suppressed_keys /
            # _strategy_blanks are label-form; the glue normalises, so they match its
            # own column lookup. (Customer's blank set is Batch Identifier alone, so
            # this cannot accidentally starve a linkage-reference column.)
            _prot: set = set(suppressed_keys) | set(_strategy_blanks)
            if sfields:
                _, _dec = _sheet_decisions(sfields)
                _prot |= {_header_label(f) for f in sfields if f.field_name in _dec} | _dec
            apply_to_frame(frame, source_system=_cust_src, batch_id=_cust_batch,
                           ref=_ref, level="account",
                           sheet_name=sheet_name, protected=_prot)
        except Exception:  # noqa: BLE001
            pass

    _ref_cache: list = []

    # Gap-1: which interface sheets should carry DATA rows. A naive fan-out wrote
    # 5,599 rows into ALL 19 sheets, so optional entity tables (Contacts,
    # Relationships, Classifications, Pay Method…) got thousands of rows of pure
    # linkage glue with no real content — junk Oracle would reject. Rule, matching
    # the gold file: the party/account/site/profile BACKBONE always emits; every
    # other sheet emits only when a real source column is actually mapped into it.
    # Suppressed sheets are still written as headers-only (0 rows) so the file set
    # stays complete, exactly like the empty tabs in a hand-filled template.
    _CUST_BACKBONE = {
        "hzimppartiest", "hzimppartysitest", "hzimppartysiteusest", "hzimplocationst",
        "hzimpaccountst", "hzimpacctsitest", "hzimpacctsiteusest", "racustomerprofilesintall",
    }
    _ITEM_BACKBONE = {"egpsystemitemsinterface"}
    # The backbone for THIS object — the sheet(s) that always carry data. Only
    # the linked Customer/Item fan-outs suppress their optional child sheets;
    # every other object writes all its sheets exactly as before.
    _backbone_keys = _CUST_BACKBONE if _is_customer else (_ITEM_BACKBONE if _is_item else set())
    _suppress_optional = _is_customer or _is_item
    _mapped_field_ids = {
        tid for tid, m in _best_m.items()
        if getattr(m, "source_column", None) and (m.status not in ("not_applicable", "rejected"))
    }
    # A field the ANALYST gave a constant to is content too. Requiring a source
    # column meant a sheet whose whole contribution is constants was written
    # headers-only — zero rows — and a default written onto zero rows is invisible.
    # That is the shape of three 31-Jul issues: Role Type = CONTACT reached
    # HZ_IMP_ACCTCONTACTS_T (which has mapped columns) and not HZ_IMP_CONTACTROLES
    # (which does not); Relationship Type and Relationship Code reached neither
    # HZ_IMP_RELSHIPS_T nor HZ_IMP_ACCOUNTRELS.
    #
    # ANY decided constant switches an optional interface on, whoever recorded it.
    #
    # This used to be person-set only, to stop an engine-seeded default reopening a
    # child table. Reviewed 05-Aug against the produced files and reversed, because
    # the narrow rule was silently dropping four constants the analyst had set:
    #
    #     Role Type = CONTACT             -> HZ_IMP_CONTACTROLES   never shipped
    #     Relationship Type = CUSTOMER    -> HZ_IMP_RELSHIPS_T     never shipped
    #     Relationship Code = CONTACT_OF  -> HZ_IMP_RELSHIPS_T     never shipped
    #     Account Relationship Set = STANDARD                      never shipped
    #
    # all recorded `approved_by="learning-engine"`, which is how a constant reaches
    # a conversion once it is in the library — so in practice the narrow rule
    # dropped everything except a value typed into this very conversion's grid.
    # Tejaswini asked for exactly this on 31-Jul: "Role Type … set … for both
    # target sheets, however it only reflects in the HZ_IMP_ACCTCONTACTS_T sheet
    # and not in HZ_IMP_CONTACTROLES."
    #
    # The blast radius is bounded and was measured before the change: of the 15
    # interfaces NextPower loads, 11 already emitted and these 4 did not. The four
    # the analyst EXCLUDED are held out by load scope, which is a different
    # mechanism and unaffected — a constant set on one of those still ships
    # nowhere, by design.
    _analyst_const_field_ids = {
        tid for tid, m in _best_m.items() if analyst_default(m) is not None
    }

    def _sheet_carries_data(s) -> bool:
        if not _suppress_optional:
            return True
        key = re.sub(r"[^a-z0-9]", "", (s.sheet_name or "").lower())
        if key in _backbone_keys:
            return True
        # An optional child table emits data when a real source column is mapped into
        # it, OR when the analyst has typed a constant for one of its fields;
        # otherwise it's written headers-only (empty tab).
        return any(f.id in _mapped_field_ids or f.id in _analyst_const_field_ids
                   for f in fields_by_sheet.get(s.id, []))

    def _headers_only(sfields: list) -> pd.DataFrame:
        cols = _dedup([_header_label(f) for f in sfields])
        return pd.DataFrame(columns=cols)

    def _apply_supplier_layout(sdf: pd.DataFrame, sheet_name: str,
                               with_end: bool = True,
                               batch_id_first: bool = False) -> pd.DataFrame:
        # Delegate to the pure, unit-tested module (reorder to the analyst tab
        # sequence + END terminator); no-op for non-supplier objects.
        # END is a CSV record terminator for the FBDI loader — it must NOT be
        # written into an Excel workbook (the real Oracle template has no END
        # column), so the xlsx branch passes with_end=False.
        return _supplier_layout(sdf, sheet_name, _is_supplier, with_end=with_end,
                                batch_id_first=batch_id_first)

    def _apply_customer_layout(sdf: pd.DataFrame, sheet_name: str,
                               for_csv: bool = True,
                               with_end: bool | None = None) -> pd.DataFrame:
        # Customer Import ships its CSVs in a column order that differs from the
        # worksheet order on three of fifteen interfaces. The counts match, so a
        # file in the wrong order is indistinguishable from a correct one by eye —
        # it just loads every value into the neighbouring column.
        #
        # with_end appends the END record terminator and MUST be forwarded: the
        # CSV branch passes it explicitly. A wrapper that swallowed it raised
        # TypeError at generation time and failed every Customer conversion —
        # the source-text test above only proved the CALL was written, not that
        # the callee could accept it. Default None means "follow for_csv", which
        # is the module's own default.
        return _customer_layout(sdf, sheet_name, _is_customer, for_csv=for_csv,
                                with_end=with_end)

    def _apply_bom_layout(sdf: pd.DataFrame, sheet_name: str) -> pd.DataFrame:
        # BOM ships ONE column order for both the worksheet and the CSV, and no
        # record terminator. The supplier package appends one, and this call sits in
        # the same generator that writes the supplier package, so the flag is passed
        # explicitly rather than left to a default — an END here would hand Oracle
        # an extra field it does not expect on these four interfaces.
        return _bom_layout(sdf, sheet_name, _is_bom, with_end=False)

    def _customer_sheet_sort(sheets_in: list) -> list:
        # Oracle rejects a child row whose parent has not loaded, so the files go
        # into the zip parents-first: Parties, Party Sites, the account layers,
        # then the contact layers. Any sheet the sequence does not name keeps its
        # existing relative position at the end rather than being dropped.
        seq = [_norm_hdr(x) for x in _customer_sequence()]
        if not seq or not _is_customer:
            return sheets_in

        def key(sh):
            k = _norm_hdr(_safe_sheet_name(sh.sheet_name))
            return (seq.index(k) if k in seq else len(seq))
        return sorted(sheets_in, key=key)

    def _write_all() -> tuple[str, str, int, int]:
        """Serialize to disk, STREAMING one interface sheet at a time so peak memory
        stays at ~one sheet — not all 19 at once, which OOM'd the worker on a large
        Customer load. Runs in a worker thread so it never blocks the event loop.
        CSV (the bundle default, and the format a real FBDI load ingests) writes one
        file per sheet into a .zip; XLSX keeps the shipped populated-template format.
        """
        nonlocal _ref_cache
        total_rows = 0
        total_cols = 0
        multi = bool(sheets_with_fields)
        # The positional reference series is the single-source linkage. The
        # multi-source merge links by entityid per sheet instead (see _cust_apply),
        # so skip building it — and skip the extra party-sheet finalize it costs.
        _ref_cache = [] if _grain_merge else _cust_ref()
        # Header row inclusion. The user toggle always wins. Otherwise the default
        # follows the FORMAT, not the object: an Excel/filled-template download is
        # for humans and keeps its header row, while CSV (and the zipped CSV
        # bundle) is the machine-readable FBDI load file and must be headerless —
        # Oracle reads a header line as a data row and rejects it. Previously this
        # keyed off _is_supplier, so non-supplier objects shipped CSVs WITH a
        # header and had to be hand-edited before loading.
        _hdr = include_header if include_header is not None else fmt in ("xlsx", "template")

        # Filled Oracle template: write each sheet's finalized frame INTO the real
        # bundled workbook (macros/instructions/formatting preserved), clearing the
        # shipped sample rows. No supplier reorder/END here — the Oracle template
        # already defines the column order and has no END terminator row; the
        # analyst runs the template's own CSV-generation macro for the load files.
        if fmt == "template" and _template_src_path:
            from app.services.template_fill_service import fill_template
            frames: dict[str, pd.DataFrame] = {}
            # Customer loads only 15 of the template's 19 interfaces (Tejaswini,
            # 31-Jul — same scope the CSV package applies below). This filled-template
            # path did NOT apply it, so the FBDI .xlsm shipped all 19 interface tabs
            # (ACCOUNTRELS, CLASSIFICS_T, RA_CUST_PAY_METHOD, RA_CUSTOMER_BANKS filled
            # too) while the CSV bundle had 15 — the two downloads of the same object
            # disagreeing. Filter the frames to the in-scope sheets AND add the
            # out-of-scope ones to drop_sheets, because the tab exists in Oracle's
            # template whether or not we hand it a frame, so dropping the frame alone
            # would still leave an (empty) tab.
            _tpl_sheets = sheets_with_fields
            _drop = set(_strategy_sheets_to_drop())
            if _is_customer:
                _tpl_sheets = [s for s in sheets_with_fields
                               if _customer_in_scope(s.sheet_name)]
                # fill_template matches drop_sheets on a NORMALISED name (punctuation
                # stripped, lowercased), the same as _strategy_sheets_to_drop returns —
                # so normalise here too, or "HZ_IMP_ACCOUNTRELS" would never match the
                # workbook's tab and the drop would silently do nothing.
                _drop |= {re.sub(r"[^a-z0-9]", "", s.sheet_name.lower())
                          for s in sheets_with_fields
                          if not _customer_in_scope(s.sheet_name)}
            if multi:
                for s in _tpl_sheets:
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf, s.sheet_name, fields_by_sheet[s.id])
                        # BOM: apply the SAME grain reshape + Item Sequence renumber the
                        # CSV package applies (below), so the filled .xlsm agrees with the
                        # CSV bundle instead of shipping un-deduped rows with a blank Item
                        # Sequence. Without this, BOM-01 fanned out to the CSV but not the
                        # template. Defensive: never fail the workbook on a reshape error.
                        if _is_bom and _is_bom_sheet(s.sheet_name):
                            try:
                                from app.services.bom_structure_service import reshape_for_sheet
                                sdf = reshape_for_sheet(sdf, s.sheet_name)
                            except Exception:  # noqa: BLE001
                                log.exception("BOM reshape (template) failed for %s", s.sheet_name)
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    frames[s.sheet_name] = sdf
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
            else:
                fdf = _finalize(fields) if fields else _apply_control_defaults(df)
                # single-sheet: key by the only data sheet's name if known
                _only = sheets_with_fields[0].sheet_name if sheets_with_fields else obj_name
                frames[_only] = fdf
                total_rows, total_cols = len(fdf), len(fdf.columns)
            # Sheets the analyst has ruled out of this workbook entirely — see
            # strategy_overlay.sheets_to_drop — plus (for Customer) the 4 interfaces
            # this client does not load. Passed here rather than filtered out of
            # `frames`, because the tab exists in Oracle's template whether or not we
            # have a frame for it, so dropping the frame alone would still leave the tab.
            _data = fill_template(_template_src_path, frames, drop_sheets=_drop)
            _stem = Path(_template_src_path).stem
            name = f"{_stem}.xlsm" if _template_src_path.lower().endswith(".xlsm") else f"{_stem}.xlsx"
            path = out_dir / name
            path.write_bytes(_data)
            return name, str(path), total_rows, total_cols

        if fmt == "xlsx":
            name = f"{obj_name}_{ts}.xlsx"
            path = out_dir / name
            # Customer: the same 15-of-19 load scope the CSV/template paths use, so an
            # .xlsx download does not carry the 4 interfaces this client never loads.
            _xlsx_sheets = ([s for s in sheets_with_fields if _customer_in_scope(s.sheet_name)]
                            if _is_customer else sheets_with_fields)
            with pd.ExcelWriter(path, engine="openpyxl") as xw:
                if multi:
                    for s in _xlsx_sheets:
                        if _sheet_carries_data(s):
                            sdf = _finalize(fields_by_sheet[s.id])
                            _cust_apply(sdf, s.sheet_name, fields_by_sheet[s.id])
                        else:
                            sdf = _headers_only(fields_by_sheet[s.id])
                        # NO supplier CSV layout here. The two outputs follow
                        # different rules, and conflating them was the bug:
                        #   FBDI / Excel -> the ORIGINAL Oracle template's own
                        #     column structure. `_finalize` already reindexes to
                        #     the template's field sequence, so applying the
                        #     analyst CSV order on top rewrote it into CSV shape
                        #     (which is what made the "FBDI" download look like
                        #     the CSV, and shifted Batch ID out of column 1).
                        #   CSV -> the analyst column order + END terminator,
                        #     applied in the CSV branches below.
                        # Nothing to do: sdf is already in template order.
                        pass
                        sdf.to_excel(xw, index=False, header=_hdr, sheet_name=_safe_sheet_name(s.sheet_name)[:31])
                        total_rows = max(total_rows, len(sdf))
                        total_cols += len(sdf.columns)
                        del sdf
                else:
                    fdf = _finalize(fields) if fields else _apply_control_defaults(df)
                    fdf.to_excel(xw, index=False, header=_hdr, sheet_name=(_safe_sheet_name(obj_name)[:31] or "Sheet1"))
                    total_rows, total_cols = len(fdf), len(fdf.columns)
            return name, str(path), total_rows, total_cols

        # CSV
        # Supplier FBDI load package (analyst / Tejaswi spec): HEADERLESS CSVs
        # (data rows only, each terminated with END), columns in the tab's
        # extraction order, each file named for its interface and packaged in a
        # zip named for the entity — even a single-sheet interface gets a zip.
        _primary = sheets_with_fields[0] if sheets_with_fields else None
        _zbase = _zip_name_for(_primary.sheet_name) if _primary else None
        if _is_supplier and _zbase:
            import zipfile as _zip
            name = f"{_zbase}.zip"
            path = out_dir / name
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for s in sheets_with_fields:
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf, s.sheet_name, fields_by_sheet[s.id])
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    sdf = _apply_supplier_layout(sdf, s.sheet_name)
                    cbase = _csv_name_for(s.sheet_name)
                    # Oracle FBDI CSVs are headerless by default (data only, END
                    # last); the user's Include-header toggle can force headers on.
                    zf.writestr(f"{cbase}.csv", sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        # Customer FBDI load package: the same shape as the supplier one, but the
        # column order and the file names come from the Customer sequence spec.
        if _is_customer and _customer_sequence() and len(sheets_with_fields) > 1:
            import zipfile as _zip
            name = f"CustomerImport_{ts}.zip"
            path = out_dir / name
            # 15 of the template's 19 interfaces. Tejaswini, 31-Jul: "they are
            # working on 15 files only, mentioned in the sheet, so we do not have to
            # generate all of the 19 FBDI output files." The four dropped are named
            # in the spec file with their reason, and logged here — a deliverable
            # that quietly got smaller is worse than one that says so.
            _in_scope = [s for s in _customer_sheet_sort(sheets_with_fields)
                         if _customer_in_scope(s.sheet_name)]
            _dropped = [s.sheet_name for s in sheets_with_fields
                        if not _customer_in_scope(s.sheet_name)]
            if _dropped:
                log.info("Customer package: %d of %d interfaces written; not loaded "
                         "by this client: %s", len(_in_scope), len(sheets_with_fields),
                         ", ".join(sorted(_dropped)))
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for _i, s in enumerate(_in_scope, 1):
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf, s.sheet_name, fields_by_sheet[s.id])
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    # V2_2 of the sequence workbook spells the terminator out: every
                    # one of the 15 CSV columns now ends with an explicit END row.
                    # The supplier package has always written it; this one never did.
                    sdf = _apply_customer_layout(sdf, s.sheet_name, for_csv=True,
                                                 with_end=True)
                    cbase = _customer_csv_name(s.sheet_name) or _safe_sheet_name(s.sheet_name)
                    # Numbered so the load ORDER survives a directory listing — the
                    # sequence is part of the deliverable, and an analyst unzipping
                    # into a folder otherwise gets them alphabetically.
                    zf.writestr(f"{_i:02d}_{cbase}.csv",
                                sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        # BOM FBDI load package: the spec's column order, Oracle's own CSV file
        # names, and NO record terminator. Written before the generic multi-sheet
        # branch below because that one applies the SUPPLIER layout, which is a
        # no-op on a BOM object and would have shipped these four interfaces in
        # whatever order the template happened to hold.
        # The branch needs a sheet the spec actually names, not just a matching
        # object name: an object called BOM whose sheets are something else must
        # keep the generic package rather than be renamed into Oracle's BOM files.
        if _is_bom and any(_is_bom_sheet(s.sheet_name) for s in sheets_with_fields):
            import zipfile as _zip
            name = f"BOMImport_{ts}.zip"
            path = out_dir / name
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for s in sheets_with_fields:
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        # Reshape the flat extract to THIS interface's grain: dedup
                        # per the validation doc's uniqueness key, keep only lines
                        # with a substitute on the Substitutes tab, and number Item
                        # Sequence 10/20/30 per structure. Before this every tab
                        # carried all ~20k source rows (NEXTPOWER BOM feedback,
                        # 05-Aug). Defensive: a tab it does not recognise, or one
                        # missing a key column, is returned unchanged.
                        try:
                            from app.services.bom_structure_service import reshape_for_sheet
                            sdf = reshape_for_sheet(sdf, s.sheet_name)
                        except Exception:  # noqa: BLE001 — never fail the load on reshape
                            log.exception("BOM reshape failed for %s", s.sheet_name)
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    sdf = _apply_bom_layout(sdf, s.sheet_name)
                    # Oracle matches the file inside the zip by NAME, so the spec's
                    # spelling is written verbatim with nothing prefixed onto it.
                    _bname = _bom_csv_name(s.sheet_name)
                    zf.writestr(_bname or f"{_safe_sheet_name(s.sheet_name)}.csv",
                                sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        if multi and len(sheets_with_fields) > 1:
            import zipfile as _zip
            name = f"{obj_name}_{ts}.zip"
            path = out_dir / name
            with _zip.ZipFile(path, "w", _zip.ZIP_DEFLATED) as zf:
                for i, s in enumerate(sheets_with_fields, 1):
                    if _sheet_carries_data(s):
                        sdf = _finalize(fields_by_sheet[s.id])
                        _cust_apply(sdf, s.sheet_name, fields_by_sheet[s.id])
                    else:
                        sdf = _headers_only(fields_by_sheet[s.id])
                    sdf = _apply_supplier_layout(sdf, s.sheet_name)
                    zf.writestr(f"{i:02d}_{_safe_sheet_name(s.sheet_name)}.csv", sdf.to_csv(index=False, header=_hdr))
                    total_rows = max(total_rows, len(sdf))
                    total_cols += len(sdf.columns)
                    del sdf
            return name, str(path), total_rows, total_cols

        sfields = fields_by_sheet[sheets_with_fields[0].id] if multi else fields
        fdf = _finalize(sfields) if sfields else _apply_control_defaults(df)
        _sname = (sheets_with_fields[0].sheet_name if multi else obj_name)
        fdf = _apply_supplier_layout(fdf, _sname)
        # Single-file fallback. A BOM conversion normally leaves through the package
        # branch above; this closes the path where it does not, so there is no route
        # to a BOM CSV that skipped the column order.
        fdf = _apply_bom_layout(fdf, _sname)
        name = f"{obj_name}_{ts}.csv"
        path = out_dir / name
        fdf.to_csv(path, index=False, header=_hdr)
        return name, str(path), len(fdf), len(fdf.columns)

    _write_t0 = _time.monotonic()
    out_name, out_path_str, total_rows, total_cols = await asyncio.to_thread(_write_all)
    log.info("generate phase — %s: write %s took %.1fs",
             obj_name, fmt, _time.monotonic() - _write_t0)
    out_path = Path(out_path_str)
    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(out_path),
        output_file_name=out_name, row_count=total_rows, column_count=total_cols,
        status="generated", dq_report=dq_report,
    )
    await artefact.insert()
    await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
    # Learning capture: persist the trustworthy mappings/defaults as reusable
    # object-level learnings (best-effort). Skipped for heavy multi-sheet objects —
    # capturing across ~1200 fields is hundreds of Mongo upserts per generate and
    # contributes nothing to the file that was just written; it's what made the
    # request hang. Their learnings are already captured when gold is applied.
    #
    # NOT AWAITED. It is one Mongo upsert per mapped field, in sequence, and the
    # file it contributes nothing to is already on disk by the time it starts.
    #
    # Measured live on 05-Aug: a Supplier Site generate wrote its artifact at 150s
    # and the API went on reporting "generating" past 410s. The file was complete
    # and downloadable for that entire four minutes — I downloaded it while the
    # poller still said the run was in progress. Every second of that was this
    # pass. The skip above already concedes the point for objects over 300 fields;
    # Supplier Site has 211, so it paid in full.
    #
    # The deliverable is the artifact. Learning capture is a side effect, and a
    # side effect does not get to hold the deliverable hostage — so it runs after
    # the return, and a failure is logged rather than raised. If the worker dies
    # mid-capture, the learnings are lost and the file is not, which is the right
    # way round; nothing downstream reads them synchronously.
    if not _heavy:
        async def _capture() -> None:
            try:
                from app.services.learning_service import capture_learnings_from_conversion
                await capture_learnings_from_conversion(conversion)
            except Exception:  # noqa: BLE001
                # Silence here means nothing was learned from a completed
                # conversion and the analyst is never told.
                log.exception("capture_learnings_from_conversion failed for conversion "
                              "%s — nothing was learned from this generate",
                              conversion.id)

        try:
            asyncio.get_running_loop().create_task(_capture())
        except RuntimeError:
            # No running loop (a script or a sync test). Do it inline rather than
            # dropping it — correctness first where there is nobody waiting.
            await _capture()
    return artefact


async def build_merged_frame_for_object(project_id, target_object: str, max_rows: int | None = None):
    """Convert EVERY bound conversion in the project that targets ``target_object``
    (each with its OWN mapping — so heterogeneous sources like eBOS + NetSuite map
    correctly), then converge them into one frame via survivorship de-dup. Returns
    (merged_df, carrier_conversion, source_names). carrier is used only for the
    shared template/sheets/naming (all conversions of one object share a template)."""
    from beanie import PydanticObjectId as _OID
    from app.models.conversion import Conversion as _Conv
    from app.models.dataset import Dataset as _DS
    convs = await _Conv.find(
        _Conv.project_id == _OID(str(project_id)),
        _Conv.target_object == target_object,
    ).sort(+_Conv.planned_load_order).to_list()
    convs = [c for c in convs if c.template_id and (c.source_dataset_ids or
             getattr(c, "source_type", "") == "ebs")]
    if not convs:
        return None, None, []
    # A Customer load is several files describing the SAME customers at different
    # grains (master, addresses, contacts), all keyed by `entityid`. Carry that key
    # and tag each source's grain so generation can give every interface sheet its
    # own rows and link them by customer instead of by row position. Other objects
    # keep the plain converge + de-dup.
    from app.services import customer_merge as _cm
    _is_customer = "customer" in (target_object or "").lower()
    # Cross-grain enrichment lookup (Customer only). A Customer load's columns live on
    # DIFFERENT source files — person names in the contact file, companyname/startdate/
    # datecreated in the master — but the rules that read them run on a different grain.
    # Build a {borrowable col -> {entityid -> value}} map across ALL raw sources up
    # front, so each source can be filled by entityid before it is converted. Reads only
    # entityid + the borrowable columns, so this pre-pass stays cheap. REC-05/07/08.
    _enrich: dict | None = None
    if _is_customer:
        from app.services.dataset_file_store import materialize_dataset_file as _mat
        _keep_norm = {_cm._norm(x) for x in (("entityid",) + _cm.BORROWABLE_SRC_COLS)}
        _raw_small: list = []
        for c in convs:
            for did in c.source_dataset_ids:
                _ds = await _DS.get(did)
                _p = await _mat(_ds) if _ds else None
                if not _p:
                    continue
                try:
                    _rf = parse_tabular(str(_p), file_type=_ds.file_type, nrows=max_rows)
                except Exception:  # noqa: BLE001 — an unreadable source contributes nothing
                    continue
                _cols = [col for col in _rf.columns if _cm._norm(col) in _keep_norm]
                if _cols:
                    _raw_small.append(_rf[_cols].copy())
                del _rf
        _enrich = _cm.build_entity_enrichment(_raw_small) or None
    frames, names = [], []
    for c in convs:
        _cf: dict = {}
        try:
            f, _ = await build_converted_dataframe(
                c, max_rows=max_rows,
                carry_source_cols=(["entityid", "internalid", "email", "altemail",
                                    "phone", "mobilephone",
                                    # Identity stamped deterministically by the merge,
                                    # by grain (customer_merge.set_party_identity):
                                    "companyname", "firstname", "middlename", "lastname"]
                                   if _is_customer else None),
                collect_frames=(_cf if _is_customer else None),
                enrich_by_entityid=_enrich)
        except Exception:  # noqa: BLE001 — skip an unreadable source, keep the rest
            continue
        if f is not None and len(f.columns):
            if _is_customer:
                f = f.copy()
                # Classify by the RAW SOURCE columns (companyname / addr / firstname),
                # which are unambiguous — the converted frame carries the glue's
                # reference columns and would mis-score every source the same. Fall
                # back to the converted-frame anchors only if the source columns are
                # somehow unavailable.
                _src_cols: set = set()
                for _entry in _cf.values():
                    _cols = _entry[1] if isinstance(_entry, tuple) and len(_entry) > 1 else None
                    _src_cols |= {str(x) for x in (_cols or [])}
                _g = _cm.classify_source_columns(_src_cols) or _cm.classify_frame_grain(f)
                if not _g:
                    # A source we could not place by grain still ships (it falls back
                    # to the whole-frame sheets), but log it — an unrecognised
                    # customer source is worth seeing rather than silently thinning a
                    # sheet's rows.
                    log.warning("customer merge: could not classify grain for a "
                                "source of %s (%d rows, cols=%s) — its rows fall back "
                                "to the un-reshaped sheets", target_object, len(f),
                                sorted(_src_cols)[:12])
                f[_cm.GRAIN_COL] = _g or ""
            frames.append(f)
            for did in c.source_dataset_ids:
                ds = await _DS.get(did)
                if ds:
                    names.append(ds.name)
    if not frames:
        return None, convs[0], names
    if _is_customer and len(frames) > 1:
        # Keep EVERY row — the grain-aware sheet split (and the per-sheet entityid
        # dedup) does the reduction, so a blanket survivorship de-dup here would
        # wrongly collapse a customer's many addresses/contacts before they reach
        # their sheets. Order preserved (master first) so party dedup keeps the
        # named master row.
        merged = pd.concat(frames, ignore_index=True)
    else:
        merged = _merge_dedupe(frames, target_object, REFERENCE_KEY_FIELDS) if len(frames) > 1 else frames[0]
    # REC-04: stamp the CUSTOMER's internalid (from the master rows, by entityid) onto
    # every row so Party Original System Reference becomes the customer's internalid,
    # consistent across the party and its children. No-op for non-customer / single
    # source — the threaded grain/entityid/internalid columns are absent.
    merged = _cm.set_party_ref_from_master(merged)
    return merged, convs[0], names


async def generate_merged_artifact(project_id, target_object: str, fmt: str = "csv",
                                   include_header: bool | None = None) -> ConvertedOutput:
    """Merge all per-source conversions for one interface object into ONE output
    file (merged + de-duplicated + cleansed + validated), written under the carrier
    conversion's artifact."""
    merged, carrier, _names = await build_merged_frame_for_object(project_id, target_object)
    if carrier is None:
        raise ValueError(f"No bound conversions for {target_object!r} in this project")
    if merged is None:
        merged = (await build_converted_dataframe(carrier))[0]
    return await generate_output_artifact(carrier, fmt=fmt, include_header=include_header, merged_df=merged)


async def get_output_preview(conversion: Conversion, limit: int = 50) -> dict[str, Any]:
    # Only generate the rows we actually show — previews were converting the whole
    # file (tens of thousands of rows) just to display 50.
    df, lineage = await build_converted_dataframe(conversion, max_rows=limit)
    head = df.head(limit)
    # Mirror the finalize-stage supplier e-mail masking here so the PREVIEW matches
    # the downloaded FBDI file. Generation applies the "xx" e-mail mask inside
    # _finalize (which the preview path skips), so without this the preview showed
    # raw e-mails even though the real output is masked — misleading anyone checking
    # the "don't trigger supplier notifications" rule from the preview.
    try:
        _tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
        _obj = ((_tpl.business_object if _tpl else None)
                or getattr(conversion, "target_object", "") or "")
        if "supplier" in _obj.lower():
            head = _mask_supplier_emails(head.copy())
    except Exception:  # noqa: BLE001 — preview must never fail on a cosmetic mask
        pass
    converted = int(len(df))
    # The SOURCE total, across every bound dataset. This used to read
    # `conversion.dataset_id` alone — the singular, legacy field — so a
    # multi-source conversion fell through to the length of a 50-row preview and
    # the badge reported 50. And on the conversions where it DID resolve, it
    # reported the source count under a label reading "Converted Data", which is
    # a different question: the two agree on a healthy 1:1 conversion and diverge
    # exactly when something has gone wrong, which is when anybody looks.
    source_rows = 0
    for _did in (conversion.source_dataset_ids or []):
        _ds = await Dataset.get(_did)
        if _ds and _ds.row_count:
            source_rows += int(_ds.row_count)

    # `total_rows` stays the best available estimate of the finished file — the
    # preview only converts `limit` rows, so its own length is not it — but it can
    # no longer claim rows the conversion did not produce.
    total = source_rows if (converted and source_rows) else converted
    out = {"columns": list(head.columns.astype(str)),
           "rows": head.fillna("").to_dict(orient="records"),
           "total_rows": total, "source_rows": source_rows,
           "converted_rows": converted, "preview_limit": int(limit),
           "lineage": lineage}
    if converted == 0:
        out["empty_reason"] = await _why_no_rows(conversion, df, source_rows)
    return out


async def _why_no_rows(conversion: Conversion, df, source_rows: int) -> dict:
    """Why the converted frame came out empty. ``{cause, headline, detail}``.

    Zero rows WITH columns rendered as a table header over an empty body — a
    blank panel with nothing on it, which is indistinguishable from a failed
    load, a page that has not finished, and a conversion that produced nothing.
    The distinct causes are few and each has a different fix, so the screen has to
    name which one it is rather than leave the analyst to guess. This is the same
    correction the duplicate panel needed ("no duplicates" vs "never compared")
    and the cleansing tab needed ("none found" vs "never run").

    Derived, never guessed: every branch below is read from the conversion, and
    the last one deliberately admits it does not know rather than inventing a
    cause that sounds plausible.
    """
    if not len(df.columns):
        return {
            "cause": "nothing_mapped",
            "headline": "No target column is mapped yet.",
            "detail": "Approve at least one mapping in Mapping Review, then "
                      "re-generate. Until a target field has a source column, a "
                      "default or a rule, there is nothing to write.",
        }

    # A decision the analyst recorded can legitimately remove every row. That is
    # not a fault, and it must not be reported as one.
    try:
        from app.services.decision_service import load_decisions
        decisions = await load_decisions(conversion.id)
    except Exception:                                           # noqa: BLE001
        decisions = []
    excluded = [d for d in decisions if (d.get("verdict") or "") == "exclude"]
    if excluded and source_rows and len(df.columns):
        covered = sum(len(d.get("member_keys") or []) for d in excluded)
        if covered >= source_rows:
            return {
                "cause": "excluded_by_decision",
                "headline": f"Every record is covered by an Exclude decision "
                            f"({len(excluded)} group(s), {covered} record(s)).",
                "detail": "This is a decision that was recorded, not a fault. "
                          "Clear it on the Duplicate suspects tab to bring the "
                          "records back.",
            }

    if source_rows == 0:
        return {
            "cause": "source_has_no_rows",
            "headline": "The source file has no data rows.",
            "detail": "The columns below come from the Oracle template, not from "
                      "the extract. Check the uploaded file — a sheet with only a "
                      "header, or the wrong sheet chosen at upload, both look "
                      "exactly like this.",
        }

    return {
        "cause": "none_survived",
        "headline": f"The source has {source_rows:,} record(s) and none of them "
                    f"reached the output.",
        "detail": "The mapping produced columns but no rows, so the extract was "
                  "read and then emptied — most often a decision on the Duplicate "
                  "suspects or Cleansing tab, or a source file whose real header "
                  "row sits below a title block, so the rows above it were taken "
                  "as the header. Check Lineage for which source column each field "
                  "is bound to.",
    }


async def preload_report(conversion: Conversion, sample_rows: int = 3000) -> dict[str, Any]:
    """Predictive pre-load check: build the merged converted frame and validate it
    (built-in FBDI checks + custom rules) WITHOUT writing a file, returning a
    plain-English 'what Oracle will reject and how to fix' report. Read-only."""
    from app.services.generate_dq import apply_cleansing, validate_frame, build_report, explain_report
    from app.services.dq_rule_service import load_rules
    from app.services.client_service import client_id_for_conversion
    df, _ = await build_converted_dataframe(conversion, max_rows=sample_rows)
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    fields = await FBDIField.find(FBDIField.template_id == template.id).to_list() if template else []
    obj = (template.business_object if template else None) or (conversion.target_object or "")
    cid = await client_id_for_conversion(conversion)
    cleanse_rules = await load_rules(obj, cid, "cleansing")
    val_rules = await load_rules(obj, cid, "validation")
    df2, fixes = await asyncio.to_thread(
        apply_cleansing, df, cleanse_rules,
        getattr(conversion, "cleansing_profile", None))
    tf = [{"field_name": f.field_name, "required": bool(f.required), "data_type": f.data_type,
           "max_length": f.max_length, "format_mask": f.format_mask} for f in fields]
    issues = await asyncio.to_thread(validate_frame, df2, tf, val_rules, sample_rows)
    report = build_report(issues, fixes)
    report = explain_report(report)
    report["sampled_rows"] = int(len(df2))
    report["target_object"] = obj
    return report


async def get_output_preview_by_source(conversion: Conversion, limit: int = 50) -> dict[str, Any]:
    """Per-source converted preview: convert EACH source file individually (the same
    way it will be converted before the merge) and return one preview block per
    source. The single merged/de-duplicated output is only produced at Generate; this
    lets the analyst see what each source contributes. Falls back to one block for a
    single-source or EBS conversion."""
    ids = conversion.source_dataset_ids
    _tpl = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    _obj = ((_tpl.business_object if _tpl else None) or getattr(conversion, "target_object", "") or "")
    is_supplier = "supplier" in _obj.lower()

    if len(ids) <= 1:
        p = await get_output_preview(conversion, limit=limit)
        nm = None
        if ids:
            ds = await Dataset.get(ids[0])
            nm = ds.name if ds else None
        return {"multi": False, "sources": [{"source_id": (str(ids[0]) if ids else None),
                                             "source_name": nm, **p}]}

    # Convert each source in isolation by temporarily pointing the (in-memory only)
    # conversion at one dataset at a time. Never persisted; restored in finally.
    orig_ids = list(conversion.dataset_ids)
    orig_single = conversion.dataset_id
    sources: list[dict] = []
    try:
        for did in ids:
            conversion.dataset_ids = [did]
            conversion.dataset_id = did
            df, lineage = await build_converted_dataframe(conversion, max_rows=limit)
            head = df.head(limit)
            if is_supplier:
                try:
                    head = _mask_supplier_emails(head.copy())
                except Exception:  # noqa: BLE001
                    pass
            ds = await Dataset.get(did)
            total = int(ds.row_count) if (ds and ds.row_count) else int(len(df))
            sources.append({
                "source_id": str(did), "source_name": ds.name if ds else None,
                "columns": list(head.columns.astype(str)),
                "rows": head.fillna("").to_dict(orient="records"),
                "total_rows": total, "lineage": lineage,
            })
    finally:
        conversion.dataset_ids = orig_ids
        conversion.dataset_id = orig_single
    return {"multi": True, "sources": sources}
