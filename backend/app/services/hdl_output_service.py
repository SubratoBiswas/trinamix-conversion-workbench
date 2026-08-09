"""Generate an Oracle HCM Data Loader (HDL) artifact from a Workday-style extract.

Produces a .zip of pipe-delimited ``.dat`` files (Location.dat, Job.dat,
Position.dat, PositionHierarchy.dat, Worker.dat) built from the loader schema in
``hdl_schema``. Each file is one or more component blocks:

    METADATA|Worker|EffectiveStartDate|PersonNumber|...
    MERGE|Worker|2017/01/01|1001898|...

Worker.dat carries the Worker block plus its six child components, all linked by
``SourceSystemId`` composite keys. Setup objects (Location/Job/Position) are
deduped to one record per unique natural key; PositionHierarchy is emitted
METADATA-only until the client supplies the parent/child structure.

This runs off the same conversion + dataset the FBDI path uses; the generator
router in ``output_service`` sends "Employee HDL" objects here instead of the CSV
fan-out.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import settings
from app.models.conversion import Conversion
from app.models.dataset import Dataset
from app.models.fbdi import FBDIField, FBDITemplate
from app.models.mapping import MappingSuggestion
from app.models.output import ConvertedOutput
from app.parsers import parse_tabular
from app.services.hdl_schema import (
    COUNTRY_ISO2, HDL_LOAD_ORDER, HDL_OBJECTS, object_label as _object_label,
)
from app.transformations import apply_pipeline  # the analyst's own rules

logger = logging.getLogger(__name__)

_EXCEL_EPOCH = datetime(1899, 12, 30)  # Excel/Workday serial-date origin


class _NullCtx:
    """Stands in for the outer zip when writing a workbook, so the object loop has
    one implementation rather than two that can drift apart."""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _clean(v: Any) -> str:
    """A single HDL cell: no pipes / newlines (they'd break the record), trimmed."""
    if v is None:
        return ""
    s = str(v)
    if s.strip().lower() in ("nan", "none", "nat", "null", "#n/a", "n/a"):
        return ""
    return s.replace("|", "/").replace("\r", " ").replace("\n", " ").strip()


def _hdl_date(v: Any) -> str:
    """Reformat a Workday date (Excel serial or common string) to HDL YYYY/MM/DD."""
    if v is None:
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%Y/%m/%d")
    s = str(v).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return ""
    # Excel/Workday serial number.
    try:
        if re.fullmatch(r"\d+(\.0+)?", s):
            return (_EXCEL_EPOCH + timedelta(days=int(float(s)))).strftime("%Y/%m/%d")
    except (ValueError, OverflowError):
        pass
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d",
                "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    return s  # leave as-is; validation can flag it


def _iso_country(v: Any) -> str:
    s = _clean(v)
    if not s:
        return ""
    if len(s) == 2 and s.isalpha():
        return s.upper()
    # The local COUNTRY_ISO2 covered ~22 countries, so Saudi Arabia / United Arab
    # Emirates / Israel / Chile / South Africa (SA/AE/IL/CL/ZA) fell through and
    # shipped their full name. Fall back to the comprehensive COUNTRY_TO_ISO crosswalk
    # (the same table the FBDI COUNTRY_ISO2 rule uses) so EVERY country resolves; an
    # unknown one is still left as-is rather than guessed.
    hit = COUNTRY_ISO2.get(s.lower())
    if hit:
        return hit
    try:
        from app.services.deterministic import COUNTRY_TO_ISO
        key = "".join(ch for ch in s.lower() if ch.isalnum())
        return COUNTRY_TO_ISO.get(key, s)
    except Exception:  # noqa: BLE001 — never fail a cell over the crosswalk import
        return s


# Returned by an ``analyst`` lookup that has nothing to say about a field. A
# sentinel rather than None, because None and "" are both real answers here: the
# analyst can say "ship this column empty" and that has to survive.
NO_ANSWER = object()


def _worker_letter(worker_type: str) -> str:
    """Assignment-number prefix by worker type: C for a contingent worker, E for an
    employee (and anything else). NextPower rule (Subrato, 09-Aug): "for contingent
    users it should be C+number, for employee E+number"."""
    return "C" if str(worker_type or "").strip().lower() in (
        "contingent worker", "contingent", "c") else "E"


def render_cell(spec: dict, resolve, analyst=None, type_lookup=None) -> str:
    """Compute one HDL cell from its field spec. ``resolve(field_name, source_name)``
    returns the raw source value (or None). Pure + module-level so it's unit-tested
    directly rather than through a DB-backed generation run.

    ``type_lookup`` (optional) maps a worker id (normalised, and digits-only) to that
    worker's assignment-number letter (E/C). It is used to give a MANAGER's assignment
    number the SAME prefix as that manager's own record — a contingent manager with a
    numeric id must still resolve to C, which the id format alone cannot tell.

    ``analyst(field_name)`` returns what the ANALYST set for this field in Mapping
    Review — a fixed value, "" for keep-blank, or ``NO_ANSWER``.

    IT IS CHECKED FIRST, BEFORE THE SPEC, AND THAT IS THE POINT.

    This function used to ignore it entirely. A ``const`` spec returned the
    schema's hard-coded value unconditionally, so every fixed value and every
    Keep blank an analyst set on an HDL component was stored, shown on screen,
    re-applied from the library, explained in the AI panel — and never asked for
    when the file was written. EffectiveStartDate is where it was noticed: the
    screen said 1/1/1900 and the .dat carried 1990/01/01, which is the schema
    constant. It was never about that one field. It was every constant on the
    whole HDL path.

    The schema's value is still there and still right — as the DEFAULT, for a
    conversion where nobody has said otherwise. What it is not is an override of
    a person.
    """
    if analyst is not None:
        answer = analyst(spec.get("name"))
        if answer is not NO_ANSWER:
            return _clean(answer)
    kind = spec.get("kind")
    if kind == "const":
        return _clean(spec.get("value"))
    if kind == "const_if_blank":
        # The extract wins; the constant is the open-ended fallback (strategy 9.1).
        got = _clean(resolve(spec["name"], spec.get("source")))
        return got or _clean(spec.get("value"))
    if kind == "blank":
        return ""
    if kind == "key":
        base = _clean(resolve(spec["name"], spec.get("key_source")))
        return f"{spec.get('prefix', '')}{spec.get('sep', '_')}{base}" if base else ""
    if kind == "worker_number":
        # AssignmentNumber = <E|C by THIS worker's type> + the FULL Employee Number,
        # VERBATIM. NextPower rule (Subrato, 09-Aug): the assignment number is the
        # employee number prefixed with the worker-type letter — C-100208 (contingent)
        # -> CC-100208, C85849 (contingent) -> CC85849, and the one worker whose id is
        # C-100129 -> EC-100129 (E because THAT worker's type is Employee, not the id's
        # leading C). The letter comes from the worker's TYPE, never from the id; the id
        # itself is kept whole — dashes and any leading letter included — so the number
        # matches the client's own template exactly. Earlier this stripped the id to its
        # DIGITS (C-100208 -> C100208), which dropped the "C-" the template keeps. A
        # pure-numeric id like 1200077 is unchanged: employee -> E1200077, contingent ->
        # C1200077 (verbatim == digits when the id is already all digits).
        wt = _clean(resolve("Worker Type", spec.get("type_source", "Worker Type")))
        emp = _clean(resolve(spec["name"], spec.get("key_source")))
        return f"{_worker_letter(wt)}{emp}" if emp else ""
    if kind == "manager":
        # ManagerAssignmentNumber must EQUAL the manager's OWN AssignmentNumber so the
        # supervisor link resolves: <E|C by the MANAGER's worker type> + the manager's
        # id VERBATIM — the same rule as worker_number, applied to the Manager_ID value.
        # The prefix is the MANAGER's type (looked up by manager id), not the
        # subordinate's: a contingent manager with a numeric id must still resolve to C,
        # which the id format alone cannot tell. Earlier this kept only the trailing
        # DIGITS, so a manager C-100129 became E100129 while their own record now reads
        # EC-100129 — the two no longer matched and the link broke.
        raw = _clean(resolve(spec["name"], spec.get("source")))
        if not raw:
            return ""
        # Legacy "Name (12345)" feed carried the id in parentheses; the real files carry
        # a bare Manager_ID used whole. Take the parenthesised token when present so the
        # older shape still yields the manager's NUMBER rather than their name.
        m_paren = re.search(r"\(([^)]+)\)\s*$", raw)
        mid = m_paren.group(1).strip() if m_paren else raw
        if not mid:
            return ""
        letter = spec.get("prefix", "E")
        if type_lookup:
            letter = (type_lookup.get(re.sub(r"[^a-z0-9]", "", mid.lower()))
                      or type_lookup.get(re.sub(r"\D", "", mid))
                      or letter)
        return f"{letter}{mid}"
    val = resolve(spec["name"], spec.get("source"))
    if kind == "date":
        return _hdl_date(val)
    if kind == "valuemap":
        m = spec.get("map", {})
        if m.get("kind") == "iso_country":
            return _iso_country(val)
        s = _clean(val)
        for k, mv in m.items():
            if k in ("default", "kind"):
                continue
            if isinstance(k, str) and k.lower() == s.lower():
                return _clean(mv)
        return _clean(m.get("default", s))
    return _clean(val)  # plain source


def is_hdl_conversion(template: FBDITemplate | None, conversion: Conversion | None = None) -> bool:
    """True when this object should be generated as HDL rather than FBDI."""
    from app.services.hdl_schema import HDL_BUSINESS_OBJECT
    bo = ((template.business_object if template else None)
          or (getattr(conversion, "target_object", None) if conversion else None)
          or "").strip().lower()
    if bo == HDL_BUSINESS_OBJECT.lower():
        return True
    nm = (template.name if template else "").strip().lower()
    return "hdl" in nm and ("employee" in nm or "worker" in nm or "hcm" in nm)


async def generate_hdl_artifact(conversion: Conversion, fmt: str = "dat") -> ConvertedOutput:
    """Build the Employee HDL .dat bundle for a conversion and persist it."""
    if not conversion.dataset_id:
        raise ValueError("HDL generation needs an uploaded Workday extract (no dataset linked).")
    dataset = await Dataset.get(conversion.dataset_id)
    from app.services.dataset_file_store import materialize_dataset_file
    src_path = await materialize_dataset_file(dataset) if dataset else None
    if src_path is None:
        raise ValueError("Dataset source file not found; please re-upload the dataset.")
    src = parse_tabular(str(src_path), file_type=dataset.file_type)

    # Normalized dataset-column lookup so schema source names ("Employee ID") and
    # user-mapped source columns resolve regardless of spacing/case.
    col_by_norm = {_norm(c): c for c in src.columns}

    # Per-(component, field) source override from the conversion's approved mappings
    # (a human/AI mapping wins over the schema's canonical source hint). Keyed by the
    # (normalised component, field name) pair, NOT by field name alone: the same
    # attribute (EffectiveStartDate, WorkerType, …) lives on several HDL components,
    # and one component's statement must not answer for another's.
    template = await FBDITemplate.get(conversion.template_id) if conversion.template_id else None
    override: dict[tuple[str, str], str] = {}
    # What the analyst SET, as opposed to what they pointed at: fixed values and
    # keep-blanks. Checked ahead of the schema's own constants. (component, field).
    const_override: dict[tuple[str, str], str] = {}
    # (component, field) -> when the analyst approved that fixed value. Only set for an
    # approved/overridden row that carries a date; an undated one cannot be shown
    # to be later, so it does not get to outrank a rule.
    const_at: dict[tuple[str, str], Any] = {}
    # (component, field) -> the analyst's rule pipeline, in sequence order.
    rules_by_field: dict[tuple[str, str], list] = {}
    # Field-wide fallback for a fixed value with no per-component statement — kept
    # only so a mapping that never names its component still behaves as before.
    const_override_any: dict[str, str] = {}
    if template:
        fields = await FBDIField.find(FBDIField.template_id == template.id).to_list()
        fname_by_id = {f.id: f.field_name for f in fields}
        # PER-COMPONENT scope. const_override / override are keyed by field NAME, which
        # is SHARED across HDL components (EffectiveStartDate is on Location, Job AND
        # every Worker component). So a fixed value / source set on ONE component's
        # field landed on the same-named field of EVERY component — last write wins.
        # Live 06-Aug: the 1900/1/1 the analyst wants on Location/Job EffectiveStartDate
        # overwrote the Worker-family EffectiveStartDate, which is mapped to Hire Date
        # (schema _date(_HIRE)) — so Worker/Assignment shipped 1900/1/1 instead of the
        # hire date. Key by (component, field) so each component keeps its own statement.
        from app.models.fbdi import FBDISheet as _FBDISheet
        _sheets = await _FBDISheet.find(_FBDISheet.template_id == template.id).to_list()
        _sheet_name_by_id = {s.id: (s.sheet_name or "") for s in _sheets}
        fcomp_by_id = {f.id: _norm(_sheet_name_by_id.get(getattr(f, "sheet_id", None), ""))
                       for f in fields}
        maps = await MappingSuggestion.find(
            MappingSuggestion.conversion_id == conversion.id
        ).to_list()
        # ONE row per target field, chosen by the rule the screen uses. This
        # walked every row, so a duplicate left by the suggest-mapping race could
        # bind the column to whichever one came back last — the same defect the
        # FBDI path had, on a path nobody had checked.
        # THE RULES THE ANALYST TYPED. This path never loaded them: a custom
        # transformation rule authored against a Worker/HCM field was saved,
        # listed and explained, and then no code on the way to the .dat file ever
        # asked for it. Same inert shape as the constants beside it — the FBDI
        # generator has run these since it was written, and this one simply never
        # learned to.
        from app.models.transformation import TransformationRule
        _rules = await TransformationRule.find(
            TransformationRule.conversion_id == conversion.id
        ).sort(+TransformationRule.sequence).to_list()
        for _r in _rules:
            _rfn = fname_by_id.get(_r.target_field_id)
            if not _rfn or not _r.rule_type:
                continue
            # Same per-component scope as the constants below: a rule authored on
            # one component's field is keyed to THAT component, so a CASE_WHEN on
            # WorkRelationship.WorkerType cannot also fire on Assignment.WorkerType.
            _rcomp = fcomp_by_id.get(_r.target_field_id, "")
            # The pipeline DICT key is ``config``, not ``rule_config``. apply_pipeline
            # reads ``r.get("config", {})`` (transformations/engine.py); the model
            # FIELD is ``rule_config`` but the FBDI path passes it under ``config``.
            # Built with the wrong key here, every rule reached apply_pipeline with an
            # EMPTY config: a CASE_WHEN with no branches returns its input unchanged,
            # a CONDITIONAL with no if_column returns its input unchanged — so every
            # analyst rule on the Employee HDL path was a silent no-op. Measured live
            # 06-Aug: Country CASE_WHEN (Saudi Arabia->SA) and OnMilitaryServiceFlag
            # CASE_WHEN (0->N,1->Y) both shipped the raw source value; AssignmentNumber
            # only looked right because of the schema's own _key("E") spec, not the rule.
            rules_by_field.setdefault((_rcomp, _rfn), []).append(
                {"rule_type": _r.rule_type, "config": _r.rule_config or {},
                 # WHEN the rule was written. Carried so this path can rank a rule
                 # against a fixed value by DATE, the way the FBDI path does.
                 "as_of": getattr(_r, "created_at", None)})

        from app.services.mapping_dedupe import best_mapping_by_target
        for _tid, m in best_mapping_by_target(maps).items():
            fn = fname_by_id.get(_tid)
            if not fn:
                continue
            # The component this field belongs to. Keying every statement by
            # (component, field) is the whole fix: EffectiveStartDate exists on
            # Location, Job AND every Worker component, so a fixed value keyed by
            # field NAME alone landed on all of them at once.
            comp = fcomp_by_id.get(_tid, "")
            _dv = m.default_value
            _dv = str(_dv).strip() if _dv is not None else ""
            if _dv:
                # A fixed value the analyst typed. The panel that sets it says it
                # "overrides AI and clears the source column" — so it does.
                const_override[(comp, fn)] = _dv
                # A mapping whose component could not be resolved keeps its old
                # field-wide reach (so nothing that worked before goes silent); a
                # resolved one stays on its own component, which is what stops the
                # Location/Job constant from overwriting Worker's Hire Date.
                if not comp:
                    const_override_any[fn] = _dv
                # ...and WHEN they typed it, so a rule can be ranked against it.
                # Employee is the only object that generates through this writer,
                # and it had no date test at all: a fixed value beat every rule
                # regardless of age, while in the FBDI path a newer rule wins. One
                # intent, two behaviours, decided by which loader the object uses.
                _dv_at = getattr(m, "approved_at", None)
                if _dv_at is not None and (m.status or "") in ("approved", "overridden"):
                    const_at[(comp, fn)] = _dv_at
                continue
            if m.status in ("not_applicable", "rejected"):
                # Keep blank. An explicit instruction to ship the column empty,
                # which is not the same as having nothing to say about it.
                const_override[(comp, fn)] = ""
                if not comp:
                    const_override_any[fn] = ""
                continue
            if getattr(m, "source_column", None):
                override[(comp, fn)] = m.source_column

    def _resolve_col(comp: str, field_name: str, schema_source) -> str | None:
        """Actual dataset column for a field: mapping override first, then the
        schema's canonical source (normalized match).

        The override is looked up by (component, field) so a source column mapped on
        one component's field does not answer for the same-named field of another;
        the ("", field) entry is the field-wide fallback for a mapping whose
        component could not be resolved.

        ``schema_source`` may be a LIST of candidate spellings — the first one present
        in the dataset wins. The client's real input file is not the extract tab the
        schema was written against: JobCode's canonical source was "Business Title"
        while the file carries a proper "Job Code" holding GSSSSM_VP2 — the exact
        value their own HDL template shows. One guessed spelling binds to nothing and
        fails silently, which is the whole class of "fields are not being reflected".
        """
        ov = override.get((comp, field_name)) or override.get(("", field_name))
        if ov and ov in src.columns:
            return ov
        if ov and _norm(ov) in col_by_norm:
            return col_by_norm[_norm(ov)]
        for cand in (schema_source if isinstance(schema_source, (list, tuple))
                     else [schema_source]):
            if cand and _norm(cand) in col_by_norm:
                return col_by_norm[_norm(cand)]
        return None

    def _analyst(comp: str, field_name: str):
        # Precedence, per component:
        #   1. this component's own fixed value / keep-blank wins outright;
        #   2. a source mapping ON this component means "resolve from the column
        #      here", so the field-wide fallback must not answer for it — return
        #      NO_ANSWER and let render_cell + _resolve_col read the source;
        #   3. only then the field-wide fallback, which is populated ONLY for a
        #      mapping whose component could not be resolved — so a constant on
        #      Location/Job can no longer answer for Worker's EffectiveStartDate.
        if (comp, field_name) in const_override:
            return const_override[(comp, field_name)]
        if (comp, field_name) in override or ("", field_name) in override:
            return NO_ANSWER
        if field_name in const_override_any:
            return const_override_any[field_name]
        return NO_ANSWER

    # Worker-type letter (E/C) by worker id — so a MANAGER's assignment number gets the
    # SAME prefix as that manager's own record. 172 contingent workers have numeric ids,
    # so the id format alone cannot tell E from C; only this lookup can. Keyed by both
    # the normalised id and its digits-only form, matching how render_cell looks it up.
    _emp_id_col = col_by_norm.get(_norm("Employee ID"))
    _wt_col = col_by_norm.get(_norm("Worker Type"))
    wt_letter_by_id: dict[str, str] = {}
    if _emp_id_col is not None and _wt_col is not None:
        for _eid, _wt in zip(src[_emp_id_col].astype(str), src[_wt_col].astype(str)):
            _letter = _worker_letter(_wt)
            for _k in {_norm(_eid), re.sub(r"\D", "", _clean(_eid))}:
                if _k:
                    wt_letter_by_id[_k] = _letter

    def _cell(row: pd.Series, spec: dict, comp: str) -> str:
        def _resolve(field_name: str, source_name: str | None):
            col = _resolve_col(comp, field_name, source_name)
            return row[col] if col else None
        value = render_cell(spec, _resolve, lambda _fn: _analyst(comp, _fn),
                            type_lookup=wt_letter_by_id)
        # Rules run on whatever the field ended up holding — source value, schema
        # constant or the analyst's fixed value — which is the order the FBDI
        # generator uses. A rule is a transformation OF the value, so it has to
        # see the value that was chosen rather than compete with it. Keyed by
        # (component, field) like everything else, with the ("", field) fallback
        # for a rule whose component could not be resolved.
        _fn = spec.get("name")
        _rules = rules_by_field.get((comp, _fn)) or rules_by_field.get(("", _fn))
        if not _rules:
            return value
        # LATEST DATE WINS, here too.
        #
        # A fixed value the analyst approved AFTER every rule on the field is the
        # later statement, so the rule does not get to transform it away. A rule
        # written after the value still runs, which is the FBDI behaviour and the
        # analyst's own precedence: "whichever is latest".
        _at = const_at.get((comp, _fn))
        if _at is not None:
            _rule_dates = [r.get("as_of") for r in _rules if r.get("as_of")]
            if not _rule_dates or _at > max(_rule_dates):
                return value
        try:
            return _clean(apply_pipeline(_rules, value, row=row.to_dict(),
                                         ctx={"component": spec.get("name")}))
        except Exception:  # noqa: BLE001
            # A rule that throws must not take the whole file down. The FBDI path
            # makes the same choice; the untransformed value still ships.
            logger.exception("HDL rule failed for %s", spec.get("name"))
            return value

    # ── Scope: exclude only terminated / left employees (strategy A-02 / A-03) ──
    # "In scope if ActiveStatus = Active" was implemented as an ALLOWLIST of
    # {active,a,y,...}. That drops every current employee whose status is a normal
    # in-service value the list does not happen to name — most importantly "OnLeave".
    # An employee on leave is a CURRENT employee, not a leaver, so excluding them lost
    # 27 real workers (HCM-02: 2,206 -> 2,179). The strategy excludes people who have
    # actually LEFT (inactive / terminated), so match on THAT: drop only the statuses
    # that mean the person is gone, and keep active, on-leave and anything else.
    #
    # Fails OPEN: if the column is absent, or if (implausibly) every row reads as
    # terminated, NOTHING is dropped — shipping a few leavers beats an empty file.
    _INACTIVE_VALUES = {"terminated", "term", "inactive", "leaver", "left",
                        "exemployee", "resigned", "retired", "deceased", "separated",
                        "n", "no", "false", "0"}
    _excluded_inactive = 0
    _active_col = col_by_norm.get(_norm("Active Status")) or col_by_norm.get(_norm("ActiveStatus"))
    if _active_col is not None:
        _mask = src[_active_col].map(
            lambda v: _norm(_clean(v)) not in _INACTIVE_VALUES)
        _kept = int(_mask.sum())
        if _kept:
            _excluded_inactive = int(len(src) - _kept)
            src = src[_mask]
            if _excluded_inactive:
                logger.info("HDL: excluded %d terminated/left employee row(s) "
                         "per strategy A-02/A-03 (on-leave kept)", _excluded_inactive)
        else:
            logger.warning("HDL: every row read as terminated in %r — keeping ALL "
                        "rows rather than shipping an empty load file", _active_col)
    else:
        logger.warning("HDL: no Active Status column found — every row kept; "
                    "strategy A-03 expects terminated employees to be excluded")

    def _rows_for(scope: str, dedup_source):
        if scope == "none":
            return []
        if scope == "distinct" and dedup_source:
            # Candidate list, same reasoning as _resolve_col: Location dedupes on
            # Location_Code in the real file and on Location in the extract tab.
            col = None
            for cand in (dedup_source if isinstance(dedup_source, (list, tuple))
                         else [dedup_source]):
                col = col_by_norm.get(_norm(cand))
                if col:
                    break
            if not col:
                return []
            seen: set[str] = set()
            out = []
            for _, r in src.iterrows():
                key = _clean(r[col])
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(r)
            return out
        return [r for _, r in src.iterrows()]  # "employee"

    out_dir = settings.output_path / f"conversion_{conversion.id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # TWO shapes of the same data, and the caller's fmt decides which.
    #
    # This function ignored fmt entirely, so "HDL Template" and "DAT files" both
    # produced the .dat bundle. The analyst asked for the workbook: "for HDL template
    # I am expecting output in HDL template excel with 6 tabs". Both are built from
    # the SAME rows below — one writes them as pipe-delimited text for HCM Data
    # Loader, the other lays them into worksheets that match the client's template —
    # so the two can never disagree about content, only about container.
    _as_book = str(fmt).lower() in ("template", "xlsx", "excel")
    zip_name = (f"Employee_HDL_Template_{ts}.xlsx" if _as_book
                else f"Employee_HDL_{ts}.zip")
    zip_path = out_dir / zip_name

    def _write() -> tuple[int, int]:
        # row_count = the largest per-object record count (≈ employee count from
        # Worker.dat), mirroring the FBDI path's "max rows across sheets" so the UI
        # number is meaningful; column_count = total attributes across components.
        total_rows = 0
        total_attrs = 0
        # HCM Data Loader takes ONE .dat per zip, and strategy section 11 loads the
        # objects in strict sequence — each depends on records the previous one
        # created. A single zip holding all nine forces the analyst to unpack and
        # re-zip before every step, so each object gets its own zip, numbered in
        # load order, inside the download bundle (CW_Issues #26).
        book = None
        if _as_book:
            from openpyxl import Workbook
            book = Workbook()
            book.remove(book.active)          # drop the default empty sheet

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) if not _as_book \
                else _NullCtx() as outer:
            for seq, obj in enumerate(HDL_LOAD_ORDER, start=1):
                spec = HDL_OBJECTS[obj]
                rows = _rows_for(spec["row_scope"], spec.get("dedup_source"))
                total_rows = max(total_rows, len(rows))
                lines: list[str] = []
                for comp_name, comp_fields in spec["components"]:
                    attrs = [f["name"] for f in comp_fields]
                    total_attrs += len(attrs)
                    lines.append("METADATA|" + comp_name + "|" + "|".join(attrs))
                    # Normalised once per block; matches fcomp_by_id, which keyed the
                    # analyst's statements by _norm(sheet_name) == _norm(comp_name).
                    _cn = _norm(comp_name)
                    for r in rows:
                        vals = [_cell(r, f, _cn) for f in comp_fields]
                        lines.append("MERGE|" + comp_name + "|" + "|".join(vals))
                if _as_book:
                    # One worksheet per object, named and ordered exactly as the
                    # client's HDL template — 01 Location … 06 Worker — with each
                    # pipe-delimited line split back into cells so the workbook is
                    # readable rather than one long string per row.
                    ws = book.create_sheet(_object_label(obj)[:31])
                    for ln in lines:
                        ws.append(ln.split("|"))
                    continue

                dat = "\n".join(lines) + "\n"

                inner = io.BytesIO()
                with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as iz:
                    iz.writestr(spec["dat"], dat)
                # The numeric prefix is the load sequence, so the upload order is
                # not something the analyst has to remember from the document. The
                # label is the client template's own sheet name, so the two Worker
                # passes are told apart at a glance — both contain Worker.dat,
                # because both load the same business object.
                outer.writestr(f"{seq:02d}_{_object_label(obj)}.zip", inner.getvalue())
        if _as_book:
            book.save(zip_path)
        return total_rows, total_attrs

    import asyncio
    total_rows, total_attrs = await asyncio.to_thread(_write)

    artefact = ConvertedOutput(
        conversion_id=conversion.id, output_file_path=str(zip_path),
        output_file_name=zip_name, row_count=total_rows, column_count=total_attrs,
        status="generated",
    )
    await artefact.insert()
    await conversion.set({"status": "output_generated", "updated_at": datetime.utcnow()})
    logger.info("hdl generate: %s — %d .dat blocks, %d MERGE records",
                zip_name, len(HDL_LOAD_ORDER), total_rows)
    return artefact
