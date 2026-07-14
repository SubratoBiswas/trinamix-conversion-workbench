"""Project-independent gold-standard library.

A client's approved FBDI output is knowledge about an OBJECT — "this is what a
Supplier load should look like" — not about one engagement. Trapping it inside a
conversion means it has to be re-uploaded for every project. This module lets a
gold file be uploaded on its own, before any project exists, and turns it into
reusable rules the whole tool honours.

What can be learned from the gold file ALONE (no source extract needed):
  * constant / near-constant defaults  — the value gold puts in a column every time
  * suppressions                       — columns gold deliberately leaves blank

What needs a paired source extract:
  * source→target column mappings — inferred by value-set overlap, so you have to
    have the source values to overlap against. Supply the source file and these are
    learned too; leave it out and we simply learn fewer rules rather than guessing.

Everything is written as global ``LearnedMapping`` rows keyed by target_object,
which is exactly what ``apply_learned_to_conversion`` already force-applies at
generate. So a gold file uploaded here silently improves every future conversion
of that object, with no wiring on the conversion side.
"""
from __future__ import annotations

import logging
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.models.fbdi import FBDIField, FBDITemplate, GoldStandard
from app.models.learned import LearnedMapping
from app.parsers import parse_tabular
from app.services.example_learning_service import (
    _MAP_THRESHOLD, _MAX_ROWS, _clean, _norm_key, _norm_val, _read_example,
    _save_reference_standard,
)

logger = logging.getLogger(__name__)


# A match has to clear BOTH bars. Scoring on the smaller side alone lets a file with
# three generic headers (NAME, COUNTRY, EMAIL) score 67% against a big template and
# get silently filed under the wrong object — so we also demand a meaningful number
# of columns actually line up. Below these, we return "unmatched" and let a human
# pick the template, which is the honest answer.
_MIN_OVERLAP = 5
_MIN_SCORE = 0.5


async def identify_template(example_keys: set[str]) -> tuple[Optional[FBDITemplate], float]:
    """Work out which template a gold file belongs to, from its headers alone.

    Scored against the smaller of the two header sets, so a template and a gold file
    that fills a subset of it still recognise each other. Verified to self-identify
    all 8 bundled templates at 100%, including the six near-identical supplier ones.
    """
    best: Optional[FBDITemplate] = None
    best_score = 0.0
    best_overlap = 0
    for tpl in await FBDITemplate.find_all().to_list():
        fields = await FBDIField.find(FBDIField.template_id == tpl.id).to_list()
        keys = {_norm_key(f.field_name) for f in fields if f.field_name}
        keys.discard("")
        if not keys:
            continue
        overlap = len(keys & example_keys)
        if not overlap:
            continue
        score = overlap / min(len(keys), len(example_keys))
        if score > best_score:
            best, best_score, best_overlap = tpl, score, overlap

    if best is None or best_overlap < _MIN_OVERLAP or best_score < _MIN_SCORE:
        return None, round(best_score, 3)
    return best, round(best_score, 3)


async def learn_from_gold(
    gold_path: Path,
    template: FBDITemplate,
    *,
    source_path: Optional[Path] = None,
    source_file_type: Optional[str] = None,
) -> dict:
    """Derive reusable object-level rules from a gold file (project-independent).

    Mirrors ``learn_conversion_from_example`` but writes ONLY the global
    LearnedMapping reference standards — there's no conversion to attach
    MappingSuggestions to, and that's the point: these apply to every conversion of
    the object, present and future.
    """
    fields = await FBDIField.find(FBDIField.template_id == template.id).sort("+sequence").to_list()
    ex = _read_example(gold_path)
    if not ex:
        return {"error": "No readable columns in that file."}

    target_object = template.business_object or template.name
    if not target_object:
        return {"error": "Template has no business object, so rules can't be keyed to one."}

    # Source values (optional) — only these unlock column-mapping inference.
    src_sets: dict[str, set[str]] = {}
    if source_path is not None:
        try:
            src_df = parse_tabular(str(source_path), file_type=source_file_type, nrows=_MAX_ROWS)
            for c in src_df.columns:
                vals = {_norm_val(v) for v in src_df[c].tolist()}
                vals.discard("")
                if vals:
                    src_sets[c] = vals
        except Exception as exc:  # noqa: BLE001
            logger.warning("gold library: source extract unreadable (%s)", exc)

    defaults: list[dict] = []
    suppressed: list[str] = []
    mappings: list[dict] = []
    rows = 0

    for f in fields:
        ex_vals = ex.get(_norm_key(f.field_name))
        if ex_vals is None:
            continue
        rows = max(rows, len(ex_vals))
        nonblank = [v for v in ex_vals if v]

        if not nonblank:
            # Gold has the column and deliberately leaves it empty → keep it empty
            # on every future conversion, overriding an over-eager AI mapping.
            await _save_reference_standard(target_object, f, source_column=None,
                                           default_value=None, suppress=True)
            suppressed.append(f.field_name)
            continue

        norm_vals = [_norm_val(v) for v in nonblank]
        uniq = set(norm_vals)
        top_norm, top_cnt = Counter(norm_vals).most_common(1)[0]

        if len(uniq) == 1 or (top_cnt / len(norm_vals)) >= 0.90:
            val = next(v for v in nonblank if _norm_val(v) == top_norm)  # gold's own casing
            await _save_reference_standard(target_object, f, source_column=None, default_value=val)
            defaults.append({"field": f.field_name, "value": val})
            continue

        if not src_sets:
            continue  # varies per row and we have no source to map it from — skip

        best_col, best_ratio = None, 0.0
        for c, cset in src_sets.items():
            ratio = len(uniq & cset) / len(uniq)
            if ratio > best_ratio:
                best_ratio, best_col = ratio, c
        if best_col and best_ratio >= _MAP_THRESHOLD:
            await _save_reference_standard(target_object, f, source_column=best_col,
                                           default_value=None)
            mappings.append({"field": f.field_name, "source_column": best_col,
                             "overlap": round(best_ratio, 2)})

    return {
        "target_object": target_object,
        "rows": rows,
        "defaults": defaults,
        "suppressed": suppressed,
        "mappings": mappings,
        "source_used": bool(src_sets),
    }


async def create_gold_standard(
    *,
    file_name: str,
    contents: bytes,
    name: Optional[str] = None,
    template_id: Optional[str] = None,
    source_file_name: Optional[str] = None,
    source_contents: Optional[bytes] = None,
    user_email: str = "",
) -> GoldStandard:
    suffix = Path(file_name).suffix or ".xlsx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(contents)
        gold_path = Path(tmp.name)

    src_path: Optional[Path] = None
    src_suffix = ""
    if source_contents:
        src_suffix = Path(source_file_name or "source.csv").suffix or ".csv"
        with tempfile.NamedTemporaryFile(delete=False, suffix=src_suffix) as tmp:
            tmp.write(source_contents)
            src_path = Path(tmp.name)

    try:
        template: Optional[FBDITemplate] = None
        confidence = 1.0
        if template_id:
            template = await FBDITemplate.get(template_id)
        if template is None:
            ex = _read_example(gold_path)
            template, confidence = await identify_template(set(ex.keys()))

        # Both blobs live inline in one Mongo document, and BSON caps a document at
        # 16MB. A big source extract copied alongside every gold file in a 6-file
        # batch is the realistic way to blow that. Keep the gold (it's the point of
        # the record) and drop the source copy if the pair won't fit — the learning
        # has already happened by then, so only re-learn is affected.
        _BSON_BUDGET = 12 * 1024 * 1024
        keep_source = source_contents
        source_note = None
        if source_contents and len(contents) + len(source_contents) > _BSON_BUDGET:
            keep_source = None
            source_note = (
                "The source extract was too large to store alongside this gold file, so "
                "it wasn't kept. The rules learned from it are unaffected; re-learning "
                "will need the extract supplied again."
            )

        gold = GoldStandard(
            name=name or Path(file_name).stem,
            file_name=Path(file_name).name,
            content=contents,
            size=len(contents),
            source_file_name=Path(source_file_name).name if source_file_name else None,
            source_content=keep_source,
            uploaded_by=user_email,
            match_confidence=confidence,
        )

        if template is None:
            gold.status = "unmatched"
            gold.note = (
                "Couldn't tell which FBDI template this belongs to — its headers don't "
                "overlap any loaded template. Load the matching template first, or pick "
                "one explicitly, then re-learn."
            )
            await gold.insert()
            return gold

        gold.template_id = template.id
        gold.template_name = template.name
        gold.target_object = template.business_object or template.name

        result = await learn_from_gold(
            gold_path, template,
            source_path=src_path,
            source_file_type=src_suffix.lstrip(".") or None,
        )
        if result.get("error"):
            gold.status = "error"
            gold.note = result["error"]
        else:
            gold.status = "learned"
            gold.rows = result["rows"]
            gold.defaults_learned = len(result["defaults"])
            gold.suppressed_learned = len(result["suppressed"])
            gold.mappings_learned = len(result["mappings"])
            gold.learned_at = datetime.utcnow()
            if not result["source_used"]:
                gold.note = (
                    "Learned defaults and suppressions. Add a matching source extract to "
                    "also learn source→target column mappings — those need source values "
                    "to overlap against."
                )
            elif source_note:
                gold.note = source_note
        await gold.insert()
        return gold
    finally:
        gold_path.unlink(missing_ok=True)
        if src_path:
            src_path.unlink(missing_ok=True)


async def relearn(gold: GoldStandard) -> dict:
    """Re-derive rules from the stored bytes — after a template reload or a rule-engine change."""
    if not gold.content:
        return {"error": "The original file wasn't stored, so it can't be re-learned. Re-upload it."}
    return_val = await create_gold_standard(
        file_name=gold.file_name or "gold.xlsx",
        contents=gold.content,
        name=gold.name,
        template_id=str(gold.template_id) if gold.template_id else None,
        source_file_name=gold.source_file_name,
        source_contents=gold.source_content,
        user_email=gold.uploaded_by or "",
    )
    await gold.delete()  # replaced by the fresh record
    return {"id": str(return_val.id), "status": return_val.status}


async def register_gold_from_conversion(
    conversion, *, file_name: str, contents: bytes, learned: dict,
) -> Optional[str]:
    """File a gold uploaded on a conversion into the shared library too.

    The conversion path already did the learning (against that conversion's source
    dataset, so it can infer column mappings). We're only persisting the artefact
    and the counts here — deliberately NOT re-learning, which would double the work
    and could disagree with what was just applied.
    """
    if not contents or not conversion.template_id:
        return None

    template = await FBDITemplate.get(conversion.template_id)
    if not template:
        return None
    target_object = template.business_object or conversion.target_object

    # Same file, same object → replace rather than pile up duplicates every time
    # someone re-teaches a conversion.
    existing = await GoldStandard.find_one(
        GoldStandard.file_name == Path(file_name).name,
        GoldStandard.target_object == target_object,
    )
    if existing:
        await existing.delete()

    gold = GoldStandard(
        name=Path(file_name).stem,
        target_object=target_object,
        template_id=template.id,
        template_name=template.name,
        file_name=Path(file_name).name,
        content=contents,
        size=len(contents),
        match_confidence=1.0,
        rows=0,
        # Use the *_count keys, not the lists — the lists are truncated to 60 for
        # the API response and would silently undercount a big gold file.
        defaults_learned=int(learned.get("default_count") or 0),
        suppressed_learned=int(learned.get("suppressed_count") or 0),
        mappings_learned=int(learned.get("mapped_count") or 0),
        status="learned",
        note="Uploaded from a conversion. Column mappings were inferred against that "
             "conversion's source extract.",
        learned_at=datetime.utcnow(),
    )
    await gold.insert()
    return str(gold.id)


async def orphan_rule_groups() -> list[dict]:
    """Objects taught by a gold file whose file we no longer hold.

    Before the library existed, uploading gold on a conversion learned from the file
    and then deleted it — the rules survived, the artefact didn't. Rather than
    pretend those objects have nothing, we surface them honestly: the learning is
    live and being applied, but there's no file to re-download or re-learn from.
    """
    have = {
        g.target_object for g in await GoldStandard.find_all().to_list() if g.target_object
    }
    rules = await LearnedMapping.find({"captured_from": "gold example"}).to_list()

    groups: dict[str, dict] = {}
    for r in rules:
        obj = (r.target_object or "").strip()
        if not obj or obj in have:
            continue
        g = groups.setdefault(obj, {
            "target_object": obj, "rules": 0,
            "defaults": 0, "suppressed": 0, "mappings": 0,
            "last_captured": None,
        })
        g["rules"] += 1
        if r.kind == "example_default":
            g["defaults"] += 1
        elif r.kind == "suppress_field":
            g["suppressed"] += 1
        elif r.kind == "column_mapping":
            g["mappings"] += 1
        if r.captured_at and (g["last_captured"] is None or r.captured_at > g["last_captured"]):
            g["last_captured"] = r.captured_at

    return sorted(groups.values(), key=lambda g: -g["rules"])


async def library_summary() -> dict:
    """What the library holds, and how many live rules came out of it."""
    golds = await GoldStandard.find_all().to_list()
    objects = sorted({g.target_object for g in golds if g.target_object})
    rules = await LearnedMapping.find(
        {"kind": {"$in": ["example_default", "suppress_field", "column_mapping"]},
         "captured_from": "gold example"}
    ).count()
    return {
        "gold_files": len(golds),
        "objects_covered": objects,
        "rules_from_gold": rules,
    }
