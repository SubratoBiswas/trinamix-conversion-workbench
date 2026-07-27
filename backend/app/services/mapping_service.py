"""Mapping orchestration service (async/Beanie)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from beanie import PydanticObjectId

from app.ai import get_mapping_provider
from app.ai.base import SourceColumn, TargetField
from app.models.dataset import Dataset, DatasetColumnProfile
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.conversion import Conversion
from app.parsers import parse_tabular

import re as _re
_NORMALIZE_RE = _re.compile(r"[^a-z0-9]")


def _normalize(name) -> str:
    """Collapse a column/field name to a case- and punctuation-insensitive key
    (matches the catalog seeder + learning-engine normalisation)."""
    if not name:
        return ""
    return _NORMALIZE_RE.sub("", str(name).lower())


async def _source_columns_for(dataset: Dataset) -> list[SourceColumn]:
    profs = await DatasetColumnProfile.find(
        DatasetColumnProfile.dataset_id == dataset.id
    ).sort(+DatasetColumnProfile.position).to_list()
    return [
        SourceColumn(
            name=p.column_name,
            inferred_type=p.inferred_type or "string",
            sample_values=[str(v) for v in (p.sample_values or [])],
            null_percent=p.null_percent or 0.0,
            distinct_count=p.distinct_count or 0,
            pattern_summary=p.pattern_summary,
        )
        for p in profs
    ]


async def _target_fields_for(template: FBDITemplate) -> list[TargetField]:
    fields = await FBDIField.find(
        FBDIField.template_id == template.id
    ).sort(+FBDIField.sequence).to_list()
    return [
        TargetField(
            id=str(f.id),
            field_name=f.field_name,
            description=f.description,
            data_type=f.data_type,
            max_length=f.max_length,
            required=bool(f.required),
            allowed_values=getattr(f, "allowed_values", None) or [],
            lookup_type=getattr(f, "lookup_type", None),
            default_if_blank=getattr(f, "default_if_blank", None),
        )
        for f in fields
    ]


async def _ebs_columns_with_diag(table_name: str) -> tuple[list[SourceColumn], dict]:
    """Fetch live Oracle EBS column metadata for *table_name* + a diagnostic dict.

    The diagnostic explains *why* zero columns came back (no connection, JDBC
    error, table/owner not found, ...) so the UI can surface an actionable
    message instead of a silent empty canvas.

    Robustness fixes vs. the original:
      * Connection lookup falls back to any ``oracle_ebs`` connection when none
        is flagged ``last_test_ok == True`` (discovery finds the connection by
        project_id with no such flag — so a healthy connection whose flag was
        never set would wrongly yield zero columns).
      * Column query tries owner-scoped first, then resolves an APPS/PUBLIC
        synonym to the real base-table owner, then finally any owner — so EBS
        base tables that live in a product schema (INV, AP, AR, ...) still
        resolve even though the login user is APPS.
    """
    import logging
    log = logging.getLogger(__name__)
    diag: dict = {"table": table_name, "stage": "start"}
    try:
        from app.models.v10 import SourceConnection
        conn = await SourceConnection.find_one(
            SourceConnection.system_type == "oracle_ebs",
            SourceConnection.last_test_ok == True,
        )
        if conn is None:
            # last_test_ok may simply never have been set — fall back to any
            # configured EBS connection rather than bailing.
            conn = await SourceConnection.find_one(
                SourceConnection.system_type == "oracle_ebs"
            )
            diag["used_fallback_connection"] = conn is not None
        if conn is None:
            diag["stage"] = "no_connection"
            log.warning("_ebs_columns_with_diag: no oracle_ebs SourceConnection found")
            return [], diag

        diag["connection_id"] = str(conn.id)
        diag["username"] = conn.username
        diag["last_test_ok"] = conn.last_test_ok

        import jaydebeapi
        password = conn.encrypted_password or ""
        if password.startswith("PLAIN:"):
            password = password[6:]

        # Use base_url as authoritative source (same logic as discovery.py)
        from app.routers.discovery import _jdbc_url_from_conn
        jdbc_url = _jdbc_url_from_conn(conn)

        db = jaydebeapi.connect(
            "oracle.jdbc.OracleDriver",
            jdbc_url,
            [conn.username, password],
            "/app/ojdbc11.jar",
        )
        import re
        cur = db.cursor()
        owner = (conn.username or "APPS").upper()
        tbl = table_name.upper()
        # ebs_table_hint is system-set, but guard the f-string DESCRIBE path
        # against anything that isn't a bare Oracle identifier.
        safe_tbl = bool(re.match(r"^[A-Z0-9_$#]+$", tbl))
        diag["safe_table_name"] = safe_tbl

        def _fetch(sql: str, params: list) -> list:
            cur.execute(sql, params)
            return cur.fetchall()

        # rows: list of (column_name, data_type, nullable, num_distinct)
        # 1) owner-scoped ALL_TAB_COLUMNS — richest metadata when the login
        #    schema actually owns a table/view with this exact name.
        rows = _fetch(
            """
            SELECT column_name, data_type, nullable, num_distinct
            FROM all_tab_columns
            WHERE table_name = ? AND owner = ?
            ORDER BY column_id
            """,
            [tbl, owner],
        )
        diag["owner_scoped_rows"] = len(rows)

        # 2) resolve an APPS/PUBLIC synonym to its REAL target owner+name, then
        #    read that object's columns (EBS exposes base tables/views through
        #    APPS synonyms whose target name often differs, e.g. *_VL / *_B).
        if not rows:
            syn = _fetch(
                """
                SELECT table_owner, table_name FROM all_synonyms
                WHERE synonym_name = ? AND owner IN (?, 'PUBLIC')
                ORDER BY DECODE(owner, ?, 0, 1)
                FETCH FIRST 1 ROWS ONLY
                """,
                [tbl, owner, owner],
            )
            if syn:
                syn_owner, syn_name = syn[0][0], syn[0][1]
                diag["synonym_owner"] = syn_owner
                diag["synonym_target"] = syn_name
                rows = _fetch(
                    """
                    SELECT column_name, data_type, nullable, num_distinct
                    FROM all_tab_columns
                    WHERE table_name = ? AND owner = ?
                    ORDER BY column_id
                    """,
                    [syn_name, syn_owner],
                )
                diag["synonym_scoped_rows"] = len(rows)

        # 3) any owner that has a table/view with this name
        if not rows:
            rows = _fetch(
                """
                SELECT column_name, data_type, nullable, num_distinct
                FROM all_tab_columns
                WHERE table_name = ?
                ORDER BY owner, column_id
                """,
                [tbl],
            )
            diag["any_owner_rows"] = len(rows)

        # 4) most robust — DESCRIBE the object by selecting zero rows. Oracle
        #    resolves synonyms/views automatically using the login schema's
        #    name resolution, so this returns columns even when the metadata
        #    views hide them. Yields names + nullability (no distinct counts).
        if not rows and safe_tbl:
            try:
                cur.execute(f"SELECT * FROM {tbl} WHERE 1 = 0")
                desc = cur.description or []
                rows = [
                    (d[0], None, ("N" if (len(d) > 6 and d[6] == 0) else "Y"), 0)
                    for d in desc
                ]
                diag["describe_rows"] = len(rows)
            except Exception as de:
                diag["describe_error"] = f"{type(de).__name__}: {de}"

        cur.close()
        db.close()

        cols = [
            SourceColumn(
                name=col_name,
                inferred_type=data_type.lower() if data_type else "string",
                sample_values=[],
                null_percent=0.0 if nullable == "N" else 50.0,
                distinct_count=int(num_distinct or 0),
                pattern_summary=None,
            )
            for col_name, data_type, nullable, num_distinct in rows
        ]
        diag["stage"] = "ok" if cols else "no_columns"
        diag["returned"] = len(cols)
        return cols, diag
    except Exception as exc:
        diag["stage"] = "error"
        diag["error"] = f"{type(exc).__name__}: {exc}"
        logging.getLogger(__name__).warning(
            f"_ebs_columns_with_diag: failed for '{table_name}': {exc}"
        )
        return [], diag


async def _source_columns_for_ebs(table_name: str) -> list[SourceColumn]:
    """Fetch column metadata from the live Oracle EBS connection for *table_name*.

    Thin wrapper over :func:`_ebs_columns_with_diag` that drops the diagnostic.
    """
    cols, _diag = await _ebs_columns_with_diag(table_name)
    return cols


async def ebs_fetch_rows(table_name: str, limit: int = 5000) -> list[dict]:
    """Fetch up to *limit* full rows from the live EBS object as plain dicts.

    Backs both transformation-rule preview (small limit) and Generate Output
    (large limit) in EBS mode — there is no uploaded file to read. The DESCRIBE
    via ``SELECT *`` follows APPS synonyms/views automatically. Returns ``[]``
    when EBS is unreachable or the table can't be resolved so callers degrade
    gracefully instead of erroring.
    """
    import logging
    import re
    try:
        from app.models.v10 import SourceConnection
        conn = await SourceConnection.find_one(
            SourceConnection.system_type == "oracle_ebs",
            SourceConnection.last_test_ok == True,
        ) or await SourceConnection.find_one(
            SourceConnection.system_type == "oracle_ebs"
        )
        if conn is None:
            return []

        tbl = (table_name or "").upper()
        if not re.match(r"^[A-Z0-9_$#]+$", tbl):
            return []
        # Cap to protect the free-tier instance from very large EBS tables.
        n = max(1, min(int(limit), 100000))

        import jaydebeapi
        password = conn.encrypted_password or ""
        if password.startswith("PLAIN:"):
            password = password[6:]
        from app.routers.discovery import _jdbc_url_from_conn
        jdbc_url = _jdbc_url_from_conn(conn)

        db = jaydebeapi.connect(
            "oracle.jdbc.OracleDriver",
            jdbc_url,
            [conn.username, password],
            "/app/ojdbc11.jar",
        )
        cur = db.cursor()
        cur.execute(f"SELECT * FROM {tbl} WHERE ROWNUM <= {n}")
        names = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
        cur.close()
        db.close()
        return [
            {names[i]: ("" if v is None else v) for i, v in enumerate(r)}
            for r in rows
        ]
    except Exception as exc:
        logging.getLogger(__name__).warning(
            f"ebs_fetch_rows: failed for '{table_name}': {exc}"
        )
        return []


async def ebs_sample_rows(table_name: str, limit: int = 5) -> list[dict]:
    """Up to *limit* (≤50) sample rows for rule preview — thin wrapper."""
    return await ebs_fetch_rows(table_name, min(int(limit), 50))


async def _sources_for_conversion(conversion: Conversion) -> list[SourceColumn]:
    """Build SourceColumn objects for a conversion (EBS live or dataset file),
    enriched with distinct values for low-cardinality columns. Shared by the
    suggestion run and the alternative-candidate ranking."""
    if getattr(conversion, "source_type", "dataset") == "ebs":
        table = getattr(conversion, "ebs_table_hint", "") or ""
        return await _source_columns_for_ebs(table) if table else []
    if not conversion.dataset_id:
        return []
    dataset = await Dataset.get(conversion.dataset_id)
    sources = await _source_columns_for(dataset)
    try:
        # Enrich low-cardinality columns with their distinct values. Read via the
        # durable store (the ephemeral file_path is wiped on redeploy) and CAP the
        # rows + run off the event loop — parsing the full wide file inline here was
        # OOM-killing the worker on small instances (surfaced as "AI fill
        # unavailable" / Failed to fetch on the ai-fill-blanks + candidate paths).
        import asyncio as _asyncio
        from app.services.dataset_file_store import materialize_dataset_file
        _path = await materialize_dataset_file(dataset)
        if _path is None:
            return sources
        df = await _asyncio.to_thread(parse_tabular, str(_path),
                                      file_type=dataset.file_type, nrows=5000)
        for sc in sources:
            if sc.name not in df.columns:
                continue
            if sc.distinct_count and sc.distinct_count > 200:
                continue
            ser = df[sc.name].dropna().astype(str).str.strip()
            vals = [v for v in ser.unique().tolist()
                    if v and v.lower() not in ("nan", "none", "null")]
            if 0 < len(vals) <= 200:
                sc.distinct_values = vals
    except Exception:
        pass
    return sources


async def mapping_candidates(
    conversion: Conversion, top_n: int = 5, target_field_id: str | None = None,
) -> list[dict]:
    """For each target field, return the top-N ranked source-column candidates
    (deterministic scorer), so the UI can offer alternatives to the auto-pick."""
    from app.ai.rule_based import rank_candidates
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    if not template:
        return []
    sources = await _sources_for_conversion(conversion)
    targets = await _target_fields_for(template)
    if target_field_id:
        targets = [t for t in targets if str(t.id) == str(target_field_id)]
    from app.ai.semantic_guard import vet_candidate
    # Load any AI verdicts previously stored for this conversion, so a return visit
    # shows the reasons with no new model call. Keyed target_field_id -> source -> v.
    from app.models.candidate_verdict import CandidateVerdict
    stored: dict[str, dict[str, dict]] = {}
    try:
        for cv in await CandidateVerdict.find(
            CandidateVerdict.conversion_id == conversion.id).to_list():
            stored.setdefault(cv.target_field_id, {})[cv.source_column] = {
                "verdict": cv.verdict, "reason": cv.reason}
    except Exception:  # noqa: BLE001
        stored = {}

    out: list[dict] = []
    for tgt in targets:
        # Over-fetch, then let the semantic guard demote nonsense before we cut to
        # top_n — otherwise an implausible 25% option (employee_id -> Phone) can
        # occupy a slot that a real, lower-scored candidate deserved.
        ranked = rank_candidates(sources, tgt, top_n=max(top_n * 2, top_n + 3))
        scored: list[dict] = []
        for score, src, reasons in ranked:
            if score <= 0:
                continue
            v = vet_candidate(src.name, list(src.sample_values or []),
                              tgt.field_name, tgt.description)
            adj = max(0.0, score - v["penalty"])
            merged = list(reasons)
            if not v["plausible"] and v["reason"]:
                merged.insert(0, f"⚠ implausible: {v['reason']}")
            cand = {
                "source_column": src.name,
                "confidence": adj,
                "raw_confidence": score,
                "plausible": v["plausible"],
                "source_category": v["source_category"],
                "target_category": v["target_category"],
                "caution": None if v["plausible"] else v["reason"],
                "inferred_type": src.inferred_type,
                "null_percent": round(src.null_percent or 0.0, 1),
                "sample_values": [str(v2) for v2 in (src.sample_values or [])[:3]],
                "reasons": merged,
            }
            sv = stored.get(str(tgt.id), {}).get(src.name)
            if sv:
                cand["ai_verdict"] = sv["verdict"]
                cand["ai_reason"] = sv["reason"]
            scored.append(cand)

        # A stored AI 'wrong'/'unlikely' sinks hardest, then the guard's implausible,
        # then by adjusted score — so a re-opened project shows the AI ranking too.
        def _rank(c: dict) -> tuple:
            av = c.get("ai_verdict")
            ai_bad = 2 if av == "wrong" else 1 if av == "unlikely" else 0
            return (ai_bad, 0 if c["plausible"] else 1, -c["confidence"])
        scored.sort(key=_rank)
        out.append({
            "target_field_id": str(tgt.id),
            "target_field_name": tgt.field_name,
            "candidates": scored[:top_n],
            "vetted": any(c.get("ai_verdict") for c in scored[:top_n]),
        })
    return out


def _looks_numeric(s) -> bool:
    s = str(s or "").strip()
    if not s:
        return False
    t = s[1:] if s[:1] in "+-" else s
    t = t.replace(",", "")
    return t.replace(".", "", 1).isdigit()


def _source_is_numeric(sc) -> bool:
    """Best-effort: is this source column numeric? Prefer the profiled type, else
    look at its sampled distinct values."""
    t = (getattr(sc, "inferred_type", None) or getattr(sc, "data_type", None) or "").lower()
    if t:
        return any(k in t for k in ("int", "num", "decimal", "float", "double"))
    vals = getattr(sc, "distinct_values", None) or []
    vals = [v for v in vals if str(v).strip()][:50]
    return bool(vals) and all(_looks_numeric(v) for v in vals)


# Text fields that must not hold a bare number (Item Class Name, descriptions).
_TEXT_NAME_TOKENS = ("name", "description", "desc")
# Boolean Y/N indicator fields that must not receive a numeric amount source
# (e.g. a Credit Limit *amount* fuzzy-mapped into the "Credit Hold" flag).
_FLAG_FIELD_TOKENS = ("hold", "flag", "indicator")


def _guard_item_mappings(results: list, targets_by_id: dict, sources: list) -> dict:
    """Conservative, Oracle-aware sanity pass on Item mappings before save.

    (a) Type guard: a numeric source mapped into a text NAME/DESCRIPTION field is
        almost certainly wrong (e.g. an Item Class *code* dropped into Item Class
        *Name*) — drop it so generate leaves the field for a correct mapping/default.
    (b) Anti-copy: when one source column is proposed for several fields, keep the
        highest-confidence target and flag the rest for review rather than blindly
        copying the same value across multiple Oracle columns.
    Mutations are best-effort (some result objects may be immutable)."""
    num_src = {getattr(s, "name", None): _source_is_numeric(s) for s in sources}
    dropped = flagged = 0

    def _set(r, **kw):
        for k, v in kw.items():
            try:
                setattr(r, k, v)
            except Exception:  # noqa: BLE001
                pass

    # (a) type guard
    for r in results:
        sc = getattr(r, "source_column", None)
        if not sc:
            continue
        tgt = targets_by_id.get(str(getattr(r, "target_field_id", "")))
        if tgt is None:
            continue
        tname = (getattr(tgt, "field_name", "") or "").lower()
        is_text_name = any(tok in tname for tok in _TEXT_NAME_TOKENS) and "number" not in tname
        if is_text_name and num_src.get(sc):
            _set(r, source_column=None, confidence=0.0, review_required=1,
                 reason="Auto-guard: dropped a numeric source from a text name/description field "
                        "(likely a code mis-mapped into a *Name* column).")
            dropped += 1
            continue
        # A numeric amount fuzzy-mapped into a Y/N flag field (e.g. Credit Limit
        # amount landing in "Credit Hold") is almost certainly wrong — drop it.
        is_flag = any(tok in tname for tok in _FLAG_FIELD_TOKENS) and "number" not in tname
        if is_flag and num_src.get(sc):
            _set(r, source_column=None, confidence=0.0, review_required=1,
                 reason="Auto-guard: dropped a numeric source from a Y/N flag field "
                        "(an amount/number does not belong in a *Hold/Flag* column).")
            dropped += 1

    # (b) anti-copy: same source used by more than one field
    best_by_src: dict = {}
    for r in results:
        sc = getattr(r, "source_column", None)
        if not sc:
            continue
        cur = best_by_src.get(sc)
        if cur is None or (getattr(r, "confidence", 0) or 0) > (getattr(cur, "confidence", 0) or 0):
            best_by_src[sc] = r
    for r in results:
        sc = getattr(r, "source_column", None)
        if not sc or best_by_src.get(sc) is r:
            continue
        _set(r, review_required=1,
             reason=((getattr(r, "reason", "") or "") +
                     " | Review: this source column is also mapped to another field — "
                     "confirm it belongs here rather than being copied across columns."))
        flagged += 1

    return {"dropped": dropped, "flagged": flagged}


async def run_mapping_suggestions(conversion: Conversion) -> list[MappingSuggestion]:
    # Self-heal: an Item conversion created AFTER the last restart can sit on a flat
    # single-sheet template (a source-named "... Item Import" that got the generic
    # 26-column itemmasterimport schema). ensure_item_multisheet runs only at startup,
    # so a conversion created between restarts is never repaired and would generate a
    # flat file. Run the idempotent repair HERE, right before mapping, so this
    # conversion is re-pointed onto the real 17-sheet Item template and its mappings
    # build against the real interface sheets — making generate emit the full FBDI.
    try:
        from app.services.template_seed_service import ensure_item_multisheet
        from app.models.conversion import Conversion as _Conv
        await ensure_item_multisheet()
        _fresh = await _Conv.get(conversion.id)
        if _fresh is not None:
            conversion = _fresh
    except Exception:  # noqa: BLE001 — never block mapping on the repair
        pass

    template = await FBDITemplate.get(conversion.template_id)

    # Determine source columns: EBS live query or static dataset
    if getattr(conversion, "source_type", "dataset") == "ebs":
        table = getattr(conversion, "ebs_table_hint", "") or ""
        sources = await _source_columns_for_ebs(table) if table else []
        if not sources:
            # EBS unreachable or no table hint — nothing to map
            import logging
            logging.getLogger(__name__).warning(
                f"run_mapping_suggestions: EBS source mode but no columns for "
                f"table='{table}' on conversion {conversion.id} — skipping"
            )
            return []
    else:
        if not conversion.dataset_id:
            return []
        dataset = await Dataset.get(conversion.dataset_id)
        sources = await _source_columns_for(dataset)
        # Value-aware mapping: attach full distinct-value lists for
        # low-cardinality columns so the matcher can score against target LOVs.
        try:
            df = parse_tabular(dataset.file_path, file_type=dataset.file_type)
            for sc in sources:
                if sc.name not in df.columns:
                    continue
                # Skip obvious identifiers to keep this cheap
                if sc.distinct_count and sc.distinct_count > 200:
                    continue
                ser = df[sc.name].dropna().astype(str).str.strip()
                vals = [v for v in ser.unique().tolist()
                        if v and v.lower() not in ("nan", "none", "null")]
                if 0 < len(vals) <= 200:
                    sc.distinct_values = vals
        except Exception:
            pass  # value-awareness is best-effort; name-based mapping still runs

    targets = await _target_fields_for(template)
    provider = get_mapping_provider()
    pname = getattr(provider, "name", "")
    # Heavy fan-out templates (19-sheet Customer ~1250 fields, 17-sheet Item ~1365)
    # have hundreds of structural/EFF slots with NO possible source column. Sending
    # that whole residual to the LLM in one synchronous batch blows the ~100s
    # gateway and the mapping request fails with ZERO mappings saved ("not mapping
    # at all"). For heavy templates, resolve deterministically + from the learning
    # library/gold only and SKIP the AI residual — the sourceless slots correctly
    # stay gaps, and the fields that matter are covered by rule match + learnings.
    _heavy = len(targets) > 300
    # Deterministic-first: the rule-based matcher (name similarity + LOV coverage
    # + sample patterns) is free and confidently maps most columns. Only the
    # targets it CAN'T place confidently are sent to the LLM — this cuts AI
    # mapping from every field to just the ambiguous/semantic residual.
    from app.ai.rule_based import RuleBasedMapper
    rule_results = RuleBasedMapper().suggest_mappings(sources, targets)
    rule_by_id = {str(r.target_field_id): r for r in rule_results}
    _CONF = 0.60
    # On very wide templates (Item, 300+ attributes) we still call the LLM for the
    # low-confidence residual, but cap it (required-first) so the batched call stays
    # bounded. Previously heavy templates skipped AI entirely — the root cause of
    # Item mappings averaging < 50% (deterministic name/keyword scoring only).
    _AI_CAP = 120

    # Learning-first: targets already covered by a learned rule (a captured
    # column mapping or suppression for this object) are resolved from the
    # learning DB afterward (apply_learned_to_conversion) — so never spend AI on
    # them. This is what makes the tool need less AI as it accumulates learnings.
    learned_targets: set[str] = set()
    try:
        from app.models.learned import LearnedMapping
        from app.services.client_service import client_id_for_conversion, scope_query
        bo = (template.business_object if template else None) or conversion.target_object
        if bo:
            _scope = await scope_query(await client_id_for_conversion(conversion))
            for lm in await LearnedMapping.find({
                "target_object": bo,
                "kind": {"$in": ["column_mapping", "suppress_field"]},
                **_scope,
            }).to_list():
                if lm.target_field:
                    learned_targets.add(lm.target_field)
    except Exception:
        learned_targets = set()

    weak = []
    for t in targets:
        if t.field_name in learned_targets:
            continue  # will be filled from the learning DB — no AI needed
        r = rule_by_id.get(str(t.id))
        if r is None or not r.source_column or (r.confidence or 0) < _CONF:
            weak.append(t)

    # Cap the residual for wide templates (required-first) so the batched AI call
    # stays bounded; narrow templates send the whole residual as before.
    weak_for_ai = weak
    if _heavy and len(weak) > _AI_CAP:
        weak_for_ai = sorted(weak, key=lambda t: (not t.required, t.field_name))[:_AI_CAP]

    ai_by_id: dict = {}
    if weak_for_ai and pname in ("anthropic", "openai"):
        if pname == "anthropic":
            from app.ai.llm_provider import anthropic_suggest_batched
            from app.config import settings
            ai_list = await anthropic_suggest_batched(
                sources, weak_for_ai,
                api_key=settings.ANTHROPIC_API_KEY,
                model=settings.ANTHROPIC_MODEL or "claude-sonnet-4-6",
            )
        else:
            ai_list = provider.suggest_mappings(sources, weak_for_ai)
        ai_by_id = {str(r.target_field_id): r for r in ai_list}

    # Merge: use the AI result for a weak target when it actually found a source;
    # otherwise keep the confident rule-based result.
    ai_results = []
    for t in targets:
        rid = str(t.id)
        r_ai = ai_by_id.get(rid)
        r_rule = rule_by_id.get(rid)
        ai_results.append(r_ai if (r_ai and r_ai.source_column) else (r_rule or r_ai))
    ai_results = [r for r in ai_results if r is not None]

    # Item quality guard (Product Hub): before saving, apply Oracle-aware sanity so
    # the AI/rule matcher doesn't blindly populate the wrong attribute:
    #  • drop a numeric source landing in a text name/description field
    #    (e.g. a code in "Item Class Name");
    #  • flag one source column being copied across several unrelated fields;
    # so downstream generate produces business-valid data, not fuzzy noise.
    _bo = (template.business_object or "").strip().lower() if template else ""
    if _bo in ("item", "item master", "customer"):
        _guard_item_mappings(ai_results, {str(t.id): t for t in targets}, sources)

    # Analyst "do-not-map" source columns: the analyst mapping docs deliberately
    # EXCLUDE certain source columns (e.g. NetSuite custom custitem_* attributes on
    # the item dump that AI otherwise over-maps). A learned ``ignore_source`` rule
    # for this object + client names each such source column; drop any mapping that
    # used one so the AI can't over-populate a field the analyst never mapped.
    try:
        from app.models.learned import LearnedMapping as _LM
        from app.services.client_service import client_id_for_conversion as _cidc, scope_query as _sq
        _obj = (template.business_object if template else None) or conversion.target_object
        if _obj:
            _scope2 = await _sq(await _cidc(conversion))
            _blocked_rows = await _LM.find({
                "kind": "ignore_source", "target_object": _obj, **_scope2
            }).to_list()
            _blocked = {_normalize(r.original_value) for r in _blocked_rows if r.original_value}
            if _blocked:
                _dropped = 0
                for r in ai_results:
                    sc = getattr(r, "source_column", None)
                    if sc and _normalize(sc) in _blocked:
                        try:
                            r.source_column = None
                            r.confidence = 0.0
                            r.review_required = 1
                            r.suggested_transformation = None
                            r.reason = ("Analyst rule: this source column is on the "
                                        "do-not-map list for this object and is left unmapped.")
                        except Exception:  # noqa: BLE001
                            pass
                        _dropped += 1
                if _dropped:
                    import logging as _lg
                    _lg.getLogger(__name__).info(
                        "do-not-map: dropped %d over-mapped source columns for %s", _dropped, _obj)
    except Exception:  # noqa: BLE001 — blocklist is best-effort, never break mapping
        pass

    # Heal any duplicate rows a prior re-run race may have left, so re-mapping
    # self-corrects and ``existing`` is one row per target field.
    try:
        _removed = await dedupe_conversion_mappings(conversion.id)
        if _removed:
            import logging as _lg
            _lg.getLogger(__name__).info(
                "dedupe: removed %d duplicate mapping rows for conversion %s",
                _removed, conversion.id)
    except Exception:  # noqa: BLE001 — heal is best-effort, never break mapping
        pass

    existing = {
        m.target_field_id: m
        for m in await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion.id
        ).to_list()
    }

    saved: list[MappingSuggestion] = []
    for s in ai_results:
        tfid = PydanticObjectId(str(s.target_field_id))
        m = existing.get(tfid)
        if m and m.status in ("approved", "rejected", "overridden", "not_applicable"):
            saved.append(m)
            continue
        if m:
            await m.set({
                "source_column": s.source_column, "confidence": s.confidence,
                "reason": s.reason, "suggested_transformation": s.suggested_transformation,
                "review_required": 1 if s.review_required else 0,
                "status": "suggested", "updated_at": datetime.utcnow(),
            })
        else:
            m = MappingSuggestion(
                conversion_id=conversion.id, target_field_id=tfid,
                source_column=s.source_column, confidence=s.confidence,
                reason=s.reason, suggested_transformation=s.suggested_transformation,
                review_required=1 if s.review_required else 0, status="suggested",
            )
            await m.insert()
        saved.append(m)

    await conversion.set({"status": "mapping_suggested", "updated_at": datetime.utcnow()})
    from app.services.learning_service import apply_learned_to_conversion
    await apply_learned_to_conversion(conversion, saved)
    return saved


# ---------------------------------------------------------------------------
# Duplicate-mapping collapse / heal
#
# A re-run race on the suggest-mapping endpoint (two overlapping calls that both
# see an empty "existing" set) can insert a SECOND MappingSuggestion for the same
# (conversion, target field). Symptoms the analyst hit: a field the human
# REJECTED still shows/exports as "suggested" (the stale twin), and one target
# appears many times in the canvas/CSV. There is no unique index on
# (conversion_id, target_field_id), so we collapse defensively on read and heal
# physically at map time / on demand. The strongest HUMAN decision always wins.
# ---------------------------------------------------------------------------
_MAP_STATUS_PRIORITY = {
    "overridden": 5, "approved": 4, "rejected": 3, "not_applicable": 2,
    "suggested": 1, "": 0,
}


def _map_ts(m) -> float:
    d = getattr(m, "updated_at", None) or getattr(m, "created_at", None)
    try:
        return d.timestamp() if d else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _dedup_key(m):
    # strongest human status, then a row that actually carries a source, then freshest
    return (
        _MAP_STATUS_PRIORITY.get((getattr(m, "status", "") or ""), 0),
        1 if getattr(m, "source_column", None) else 0,
        _map_ts(m),
    )


def collapse_mapping_dupes(items: list) -> list:
    """Return one mapping per target_field_id (strongest per ``_dedup_key``),
    preserving first-occurrence order. Non-destructive — for read/export paths."""
    best: dict = {}
    for m in items:
        k = str(m.target_field_id)
        cur = best.get(k)
        if cur is None or _dedup_key(m) > _dedup_key(cur):
            best[k] = m
    seen: set = set()
    out: list = []
    for m in items:
        k = str(m.target_field_id)
        if k in seen:
            continue
        seen.add(k)
        out.append(best[k])
    return out


async def dedupe_conversion_mappings(conversion_id) -> int:
    """Physically delete duplicate MappingSuggestion docs for the same
    (conversion, target field), keeping the strongest. Returns count removed."""
    from collections import defaultdict
    cid = (conversion_id if isinstance(conversion_id, PydanticObjectId)
           else PydanticObjectId(str(conversion_id)))
    rows = await MappingSuggestion.find(
        MappingSuggestion.conversion_id == cid
    ).to_list()
    groups: dict = defaultdict(list)
    for m in rows:
        groups[str(m.target_field_id)].append(m)
    removed = 0
    for _k, ms in groups.items():
        if len(ms) < 2:
            continue
        ms.sort(key=_dedup_key, reverse=True)
        for extra in ms[1:]:
            await extra.delete()
            removed += 1
    return removed


async def enrich_mapping_with_samples(
    conversion: Conversion, mappings: list[MappingSuggestion]
) -> list[dict[str, Any]]:
    dataset = await Dataset.get(conversion.dataset_id) if conversion.dataset_id else None
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    try:
        df = parse_tabular(dataset.file_path, file_type=dataset.file_type) if dataset else None
    except Exception:
        df = None
    if template:
        fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
    else:
        fields = []
    fields_by_id = {f.id: f for f in fields}
    out: list[dict[str, Any]] = []
    for m in mappings:
        tgt = fields_by_id.get(m.target_field_id)
        sample_src: list[Any] = []
        if df is not None and m.source_column and m.source_column in df.columns:
            sample_src = [str(v) for v in df[m.source_column].astype(str).head(5).tolist()]
        out.append({
            "id": str(m.id), "conversion_id": str(m.conversion_id),
            "target_field_id": str(m.target_field_id),
            "target_field_name": tgt.field_name if tgt else None,
            "target_required": bool(tgt.required) if tgt else False,
            "target_data_type": tgt.data_type if tgt else None,
            "target_max_length": tgt.max_length if tgt else None,
            "target_lov": (getattr(tgt, "allowed_values", None) or []) if tgt else [],
            "target_default_if_blank": getattr(tgt, "default_if_blank", None) if tgt else None,
            "source_column": m.source_column, "confidence": m.confidence,
            "reason": m.reason, "suggested_transformation": m.suggested_transformation,
            "review_required": m.review_required, "status": m.status,
            "default_value": m.default_value, "comment": m.comment,
            "approved_by": m.approved_by, "approved_at": m.approved_at,
            "sample_source_values": sample_src, "sample_converted_values": [],
        })
    return out
