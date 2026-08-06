"""Translate a plain-English rule description into a structured transformation rule.

Powers the "Describe this rule in plain English" box in the Rule Author modal:
given a conversion's source columns and the target field, turn a free-text
instruction into ``{rule_type, config, explanation, ambiguities, source}`` that the
modal drops straight into its form (the user can then confirm/edit and Save & learn).

Strategy: a fast DETERMINISTIC parse for the frequent patterns first (the
"flag column = Yes -> code, else blank" derivation, plain constants, simple
value maps) — no API cost and works offline — then the Claude API for anything
the deterministic pass can't confidently handle. Every path degrades safely: on
any error it returns a benign CONSTANT-passthrough with an explanation so the
modal never breaks.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.models.conversion import Conversion
from app.models.dataset import DatasetColumnProfile
from app.models.fbdi import FBDIField

log = logging.getLogger(__name__)

# Rule types the modal understands (kept in sync with RuleAuthorModal RULE_SPECS).
_SUPPORTED_TYPES = [
    "TRIM", "UPPERCASE", "LOWERCASE", "TITLE_CASE", "REMOVE_HYPHEN",
    "REMOVE_SPECIAL_CHARS", "REPLACE", "REGEX_REPLACE", "REGEX_EXTRACT", "PAD",
    "SUBSTRING", "DEFAULT_VALUE", "CONSTANT", "COMPUTED", "VALUE_MAP",
    "CROSSWALK_LOOKUP", "DATE_FORMAT", "NUMBER_FORMAT", "ARITHMETIC", "CONCAT",
    "SPLIT", "COALESCE", "CONDITIONAL", "CASE_WHEN", "PREFIX", "SUFFIX",
    "SELF_LOOKUP", "CROSS_CONVERSION_LOOKUP",
]

_YES_RE = re.compile(r"(?i)^(y|yes|true|1|x)$")


async def _source_columns(conversion: Conversion) -> list[str]:
    """Every column of every bound source — not just the first sheet.

    This read the singular conversion.dataset_id, so on a multi-sheet workbook the
    translator could not see (and therefore could not resolve) a column living on
    any sheet but the first. Same defect the Mapping Review column list had.
    """
    ids = [d for d in (getattr(conversion, "source_dataset_ids", None) or []) if d]
    if not ids and getattr(conversion, "dataset_id", None):
        ids = [conversion.dataset_id]
    if not ids:
        return []
    cols = await DatasetColumnProfile.find(
        {"dataset_id": {"$in": ids}}).sort("+position").to_list()
    out, seen = [], set()
    for c in cols:
        n = (c.column_name or "").strip()
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


# "X should be mapped from Y" / "map X from Y" / "X comes from Y" — the phrase that
# names the SOURCE column. Anchored on the mapping verb so the tail is the column.
# The field name can sit between the verb and the preposition — "map Address Name
# FROM City" — so an optional short phrase is allowed there. Non-greedy, so the
# FIRST preposition wins and the tail is the column.
_FROM_RE = re.compile(
    r"(?i)\b(?:mapped|map|mapping|pulled|pull|taken|take|read|comes?|sourced?)\s+"
    r"(?:\S+(?:\s+\S+){0,4}\s+)??(?:from|to|with|using|off)\s+(?P<c>.+?)\s*(?:[.;,]|$)")
_SCOPE_TAIL_RE = re.compile(
    r"(?i)\s*\b(?:for|in|across|on)\s+(?:all|every|each)\s+"
    r"(?:the\s+)?(?:conversions?|sheets?|files?|objects?|records?|rows?)\s*\.?\s*$")


def _source_from_description(desc: str, cols: list[str]) -> Optional[str]:
    """The source column the sentence names, resolved against the REAL column list.

    The translator returned rule_type and config and NOTHING about the source column,
    so the modal kept whatever was pre-filled from the mapping. Typing "Supplier Name
    should be mapped from Legal Name" set the target field correctly and left the
    source on `Name` — the analyst's report, and the screenshot.
    """
    if not desc or not cols:
        return None
    m = _FROM_RE.search(desc)
    phrase = (m.group("c") if m else "").strip()
    phrase = _SCOPE_TAIL_RE.sub("", phrase).strip().strip("\"'")
    if phrase:
        hit = _match_column(cols, phrase)
        if hit:
            return hit
    # Fallback: the LONGEST column name appearing literally in the sentence —
    # longest matters, because "Legal Name" and "Name" both appear and the specific
    # one is meant.
    #
    # Only when the sentence is actually ABOUT a binding. "trim the supplier name"
    # is a transform on the column already chosen, and returning "Name" for it would
    # silently retarget the field. No suggestion is much better than a wrong one.
    low = desc.lower()
    if not re.search(r"(?i)\b(?:from|mapped|mapping|sourced?)\b", low):
        return None
    hits = [c for c in cols if c.lower() in low]
    return max(hits, key=len) if hits else None


def _match_column(cols: list[str], phrase: str) -> Optional[str]:
    """Find the source column a phrase refers to: exact (case-insensitive) first,
    then the column whose words are all contained in the phrase (or vice-versa)."""
    if not phrase:
        return None
    p = phrase.strip().lower()
    low = {c.lower(): c for c in cols}
    if p in low:
        return low[p]
    # column name fully present in the phrase (e.g. "email transaction" -> "Email Transactions")
    best = None
    for c in cols:
        cl = c.lower()
        base = cl.rstrip("s")
        if cl in p or base in p or p in cl:
            if best is None or len(c) > len(best):
                best = c
    if best:
        return best
    # word-overlap fallback
    pw = set(re.findall(r"[a-z0-9]+", p))
    for c in cols:
        cw = set(re.findall(r"[a-z0-9]+", c.lower()))
        if cw and cw <= pw:
            return c
    return None


# A single-column instruction is not always a TRANSFORMATION. Three of the four
# things an analyst types about one column change the MAPPING and need no rule at
# all, and forcing them into a rule is what produced the nonsense in the screenshot:
# "Supplier Name should be mapped from Legal Name" came back as a VALUE MAPPING with
# the placeholder pairs active->production, which is neither what was asked for nor
# anything the analyst would keep.
_BLANK_RE = re.compile(
    r"(?i)\b(?:leave|keep)\s+(?:it\s+|this\s+)?(?:blank|empty)\b"
    r"|\b(?:should\s+be|must\s+be)\s+(?:blank|empty)\b"
    r"|\bdo\s*n[o']?t\s+(?:map|populate|fill)\b|\bblank\s+(?:it|this)\b")
_CONST_RE = re.compile(
    r"(?i)\b(?:set|default)\s+(?:it\s+|this\s+)?(?:to|=|as)\s+"
    r"['\"]?(?P<v>[A-Za-z0-9_\-/. ]{1,40}?)['\"]?\s*(?:[.;,]|$)"
    r"|\b(?:should\s+(?:always\s+)?be|always)\s+"
    r"['\"](?P<v2>[^'\"]{1,40})['\"]")
# A transformation verb means the sentence really is about a rule, so "set X to
# uppercase" is not read as the constant "uppercase".
_TRANSFORM_VERB = re.compile(
    r"(?i)\b(?:trim|upper\s*case|uppercase|lower\s*case|lowercase|title\s*case|pad|"
    r"substring|split|concat|combine|join|replace|regex|format|round|prefix|suffix|"
    r"if\b|when\b|case\b|crosswalk|lookup|date)\b")


def detect_intent(description: str, cols: list[str]) -> Optional[dict]:
    """What the sentence is ABOUT, before asking what rule it is.

    Returns {"intent": "remap"|"default"|"blank", ...} or None when it genuinely
    describes a transformation. Deterministic and free — the model still runs for
    the transformation case.
    """
    d = (description or "").strip()
    if not d:
        return None
    if _BLANK_RE.search(d):
        return {"intent": "blank",
                "explanation": "Leaves this column empty in the output, and records "
                               "the decision so it is not re-filled."}
    src = _source_from_description(d, cols)
    if src and not _TRANSFORM_VERB.search(d):
        return {"intent": "remap", "source_column": src,
                "explanation": f'Points this column at "{src}". No transformation '
                               "rule is needed — it is a mapping change."}
    if not _TRANSFORM_VERB.search(d):
        m = _CONST_RE.search(d)
        if m:
            val = (m.group("v") or m.group("v2") or "").strip()
            if val:
                return {"intent": "default", "value": val,
                        "explanation": f'Writes the constant "{val}" into this column '
                                       "on every row."}
    return None


def _local_parse(description: str, cols: list[str]) -> Optional[dict]:
    """Deterministic parse for the common patterns. Returns a rule dict or None.

    Handles the flag-derivation pattern the analysts use for Delivery Channel /
    Delivery Method: 'if <flagA> has value Yes -> <A>, if <flagB> has value Yes
    -> <B>, if both No -> blank' → a CASE_WHEN with one branch per flag."""
    d = description.strip()
    dl = d.lower()

    # --- flag → code derivation (CASE_WHEN) --------------------------------
    # Look for "if <something> ... yes ... <target field> ... <code>" clauses.
    # We anchor on the word 'if' + 'yes' and pull the column + the emitted value.
    if "if" in dl and "yes" in dl:
        branches = []
        # split into clauses on 'if' so each 'if X ... Yes ... set to Y' is separate
        clauses = re.split(r"(?i)\bif\b", d)
        for cl in clauses:
            cll = cl.lower()
            if "yes" not in cll:
                continue
            col = _match_column(cols, cll)
            if not col:
                continue
            # the emitted value: after 'set to' / 'to' / '->' / 'be', take the last
            # short token(s); default to a cleaned word.
            m = re.search(r"(?i)(?:set to|value to|target field (?:set to|would be)|->|to|be)\s+'?\"?([A-Za-z0-9_ /-]{1,30})'?\"?", cl)
            then = None
            if m:
                then = m.group(1).strip().strip(".").strip()
                # keep it to a single token/code where possible (EMAIL, FAX)
                first = then.split(",")[0].split(" and ")[0].strip()
                then = first
            if not then:
                continue
            # normalise obvious codes
            tn = then.strip().upper()
            if tn in ("EMAIL", "E-MAIL", "MAIL"):
                then = "EMAIL"
            elif tn == "FAX":
                then = "FAX"
            branches.append({"if_column": col, "op": "regex",
                             "value": "(?i)^(y|yes|true|1|x)$", "then": then})
        if branches:
            default = "" if re.search(r"(?i)(both|otherwise|else|neither).{0,40}(no|blank|empty)", d) or "blank" in dl else ""
            expl = ("Derived a Case/when rule: " +
                    "; ".join(f"{b['if_column']} = Yes → {b['then']}" for b in branches) +
                    (f"; otherwise blank" if default == "" else ""))
            return {"rule_type": "CASE_WHEN",
                    "config": {"branches": branches, "default": default},
                    "explanation": expl, "ambiguities": [], "source": "local"}

    # --- plain constant ----------------------------------------------------
    m = re.search(r"(?i)^(?:always\s+)?set\s+(?:to|it to|the (?:field|value) to)\s+'?\"?([^'\"]{1,40})'?\"?\.?$", d)
    if m:
        val = m.group(1).strip()
        return {"rule_type": "CONSTANT", "config": {"value": val},
                "explanation": f"Always emit the constant '{val}'.",
                "ambiguities": [], "source": "local"}

    return None


def _prompt(description: str, cols: list[str], target_field: Optional[str],
            source_column: Optional[str]) -> str:
    cols_blob = "\n".join(f"- {c}" for c in cols[:200]) or "(none available)"
    return (
        "You convert a plain-English data-transformation instruction into ONE structured "
        "rule for an Oracle FBDI field. Return ONLY a JSON object, no prose.\n\n"
        f"TARGET FIELD: {target_field or '(unspecified)'}\n"
        f"THIS FIELD'S SOURCE COLUMN: {source_column or '(unspecified)'}\n\n"
        "AVAILABLE SOURCE COLUMNS (use these EXACT names in any if_column/columns):\n"
        + cols_blob + "\n\n"
        f"SUPPORTED rule_type values: {', '.join(_SUPPORTED_TYPES)}\n\n"
        "CONFIG SHAPES (only the relevant keys):\n"
        '  CASE_WHEN: {"branches":[{"if_column":"<col>","op":"eq|ne|gt|lt|contains|regex|isblank|notblank","value":"<v>","then":"<out>"}],"default":"<v>"}\n'
        '  CONDITIONAL: {"if_column":"<col>","equals":"<v>","then":"<v>","else":"<v>"}\n'
        '  VALUE_MAP: {"<from>":"<to>","case_insensitive":true,"default":"<optional>"}\n'
        '  CONSTANT: {"value":"<v>"}   DEFAULT_VALUE: {"value":"<v>"}\n'
        '  CONCAT: {"columns":["a","b"],"separator":" "}   COALESCE: {"columns":["a","b"],"default":""}\n'
        '  SPLIT: {"separator":" ","index":0}   DATE_FORMAT: {"input_format":"%m/%d/%Y","output_format":"%Y/%m/%d"}\n\n'
        "RULES:\n"
        "- If the instruction tests OTHER column(s) to decide the value (e.g. 'if X is Yes set to A, "
        "if Y is Yes set to B, else blank'), use CASE_WHEN with one branch per condition and a default. "
        "For Yes/No flags prefer op 'regex' with value '(?i)^(y|yes|true|1|x)$'.\n"
        "- Only reference columns from the list above, by their exact name.\n"
        '- If the instruction names which SOURCE COLUMN the field should read '
        '("mapped from Legal Name"), include "source_column" with that EXACT column '
        "name from the list.\n"
        f"INSTRUCTION: {description}\n\n"
        'Respond with ONLY: {"rule_type":"...","config":{...},"explanation":"one sentence",'
        '"ambiguities":[{"phrase":"...","interpreted_as":"...","alternatives":["..."]}]}'
    )


async def _ai_translate(description: str, cols: list[str], target_field: Optional[str],
                        source_column: Optional[str]) -> Optional[dict]:
    from app.config import settings
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": getattr(settings, "ANTHROPIC_MODEL", None) or "claude-sonnet-4-6",
                      "max_tokens": 1200,
                      "messages": [{"role": "user",
                                    "content": _prompt(description, cols, target_field, source_column)}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            return None
        obj = json.loads(text[s:e + 1])
        rt = (obj.get("rule_type") or "").strip().upper()
        if rt not in _SUPPORTED_TYPES:
            return None
        return {"rule_type": rt, "config": obj.get("config") or {},
                "explanation": obj.get("explanation") or "", "ambiguities": obj.get("ambiguities") or [],
                "source": "ai"}
    except Exception as exc:  # noqa: BLE001
        log.warning("rule translate AI failed: %s", exc)
        return None


async def translate_rule(conversion: Conversion, description: str,
                         target_field_id: Optional[str] = None,
                         source_column: Optional[str] = None) -> dict:
    """Main entry: NL instruction -> {rule_type, config, explanation, ambiguities, source}."""
    cols = await _source_columns(conversion)
    target_field = None
    if target_field_id:
        try:
            from beanie import PydanticObjectId
            f = await FBDIField.get(PydanticObjectId(str(target_field_id)))
            target_field = f.field_name if f else None
        except Exception:  # noqa: BLE001
            target_field = None

    # The SOURCE COLUMN the sentence names. Returned on every path so the modal can
    # move the picker: the translator used to hand back rule_type and config and
    # nothing about the source, so "Supplier Name should be mapped from Legal Name"
    # set the target field and left the source on whatever was pre-filled.
    # INTENT FIRST. Three of the four things an analyst types about one column are
    # mapping changes, not transformations, and answering them with a rule form is
    # what put a Value mapping and its placeholder pairs in front of someone who
    # asked to re-point a column.
    intent = detect_intent(description or "", cols)
    if intent:
        return {**intent, "rule_type": None, "config": {},
                "ambiguities": [], "source": "local",
                **({"source_column_changed_from": source_column or None}
                   if intent.get("intent") == "remap"
                   and (intent.get("source_column") != source_column) else {})}

    named_src = _source_from_description(description or "", cols)

    def _with_src(res: dict) -> dict:
        if named_src and named_src != source_column:
            res = {**res, "source_column": named_src,
                   "source_column_changed_from": source_column or None}
        elif named_src:
            res = {**res, "source_column": named_src}
        return res

    # 1) deterministic fast-path
    local = _local_parse(description or "", cols)
    if local:
        return _with_src(local)
    # 2) Claude API
    ai = await _ai_translate(description or "", cols, target_field, source_column)
    if ai:
        # The model may name the column itself; its answer wins when it is a REAL
        # column, since it saw the whole sentence.
        _ai_src = _match_column(cols, str(ai.get("source_column") or "")) if ai.get("source_column") else None
        if _ai_src:
            return {**ai, "source_column": _ai_src,
                    "source_column_changed_from": source_column or None}
        return _with_src(ai)
    # 3) safe fallback — a pass-through constant the user can edit, never an error
    return _with_src({
        "rule_type": "CONSTANT", "config": {"value": ""},
        "explanation": ("Couldn't translate this automatically (AI unavailable). "
                        "Pick a rule type and fill it in — for conditions on other "
                        "columns use Case / when (multi-branch)."),
        "ambiguities": [], "source": "local",
    })


# ─────────────────────────────────────────────────────────────────────────────
# SQL → rule
#
# Analyst, 05-Aug: "Any SQL query written in the new text box should be understood
# by python or AI functions and it should be converted to a rule for that field."
#
# A SQL CASE expression is the same shape as the CASE/WHEN rule the analyst builds
# by hand — WHEN <condition> THEN <result>, ELSE <default> — so this parses that
# form deterministically and hands anything it cannot to the same Claude path the
# English box uses. Both return the identical {rule_type, config, ...} the modal
# already drops into its form.
# ─────────────────────────────────────────────────────────────────────────────

# SQL comparison operator -> the engine's op name (_COMPARISON_OPS).
_SQL_OPS = {
    "=": "eq", "==": "eq", "!=": "neq", "<>": "neq",
    ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
}


def _strip_sql_literal(tok: str) -> str:
    """`'Saudi Arabia'` -> Saudi Arabia; unquoted stays as-is (a column name)."""
    t = tok.strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        return t[1:-1].replace("''", "'")
    return t


def _sql_result_to_template(expr: str) -> str:
    """A THEN/ELSE result expression -> the engine's result string.

    A quoted literal is itself. A bare column becomes ``{Column}`` so the engine
    interpolates it. ``'E' || Employee_ID`` becomes ``E{Employee_ID}`` — the exact
    id-prefix case. CONCAT(a, b) is treated the same as ``a || b``.
    """
    e = expr.strip()
    m = re.match(r"(?i)^concat\s*\((.*)\)$", e)
    if m:
        parts = _split_top_level(m.group(1), ",")
    else:
        parts = _split_top_level(e, "||")
    out = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
            out.append(_strip_sql_literal(p))
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", p):
            out.append("{" + p.strip() + "}")
        else:
            # An expression we do not model (a function call, arithmetic) — keep it
            # literally rather than guess; the analyst can edit, and the AI path is
            # tried first for anything with these.
            out.append(p)
    return "".join(out)


def _split_top_level(s: str, sep: str) -> list[str]:
    """Split on ``sep`` that is not inside quotes or parentheses."""
    parts, buf, depth, i = [], [], 0, 0
    quote = None
    while i < len(s):
        ch = s[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch; buf.append(ch)
        elif ch == "(":
            depth += 1; buf.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1); buf.append(ch)
        elif depth == 0 and s[i:i + len(sep)] == sep:
            parts.append("".join(buf)); buf = []; i += len(sep); continue
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _parse_condition(cond: str, case_operand: Optional[str], cols: list[str]) -> Optional[dict]:
    """One WHEN condition -> a CASE_WHEN branch clause (no ``then`` yet).

    Handles ``col = 'x'``, ``col <> 'x'``, ``col > 5``, ``col LIKE '%x%'`` (->
    contains / startswith / endswith), ``col IS [NOT] NULL``, and the simple-CASE
    form ``CASE col WHEN 'x'`` where the operand is implicit.
    """
    c = cond.strip()
    # IS NULL / IS NOT NULL
    m = re.match(r"(?i)^(.+?)\s+is\s+(not\s+)?null$", c)
    if m:
        col = _match_column(cols, m.group(1).strip()) or m.group(1).strip()
        return {"if_column": col, "op": "notblank" if m.group(2) else "isblank",
                "value": ""}
    # LIKE
    m = re.match(r"(?i)^(.+?)\s+like\s+(.+)$", c)
    if m:
        col = _match_column(cols, m.group(1).strip()) or m.group(1).strip()
        pat = _strip_sql_literal(m.group(2))
        lead = pat.startswith("%")
        trail = pat.endswith("%")
        core = pat.strip("%")
        op = ("contains" if lead and trail else
              "endswith" if lead else "startswith" if trail else "eq")
        return {"if_column": col, "op": op, "value": core}
    # simple CASE: "CASE Country WHEN 'Saudi Arabia' THEN ..." -> operand implicit
    if case_operand and re.fullmatch(r"'.*'|\".*\"|[-\w. ]+", c):
        col = _match_column(cols, case_operand) or case_operand
        return {"if_column": col, "op": "eq", "value": _strip_sql_literal(c)}
    # binary comparison
    for sym in ("<>", "!=", ">=", "<=", "=", ">", "<"):
        idx = _find_top_level_op(c, sym)
        if idx >= 0:
            left = c[:idx].strip()
            right = c[idx + len(sym):].strip()
            col = _match_column(cols, left) or left
            return {"if_column": col, "op": _SQL_OPS[sym],
                    "value": _strip_sql_literal(right)}
    return None


def _find_top_level_op(s: str, sym: str) -> int:
    depth, i, quote = 0, 0, None
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and s[i:i + len(sym)] == sym:
            # ">=" must not match the ">" scan first: callers try longer ops first.
            return i
        i += 1
    return -1


def parse_sql_case(sql: str, cols: list[str]) -> Optional[dict]:
    """A SQL CASE expression -> a CASE_WHEN rule config, or None if it is not one.

    Deterministic, offline. Returns ``{rule_type, config, explanation, source}``
    the same shape the English translator returns.
    """
    if not sql or "case" not in sql.lower():
        return None
    m = re.search(r"(?is)\bcase\b(.*?)\bend\b", sql)
    body = m.group(1).strip() if m else sql.strip()

    # Optional simple-CASE operand: "CASE <operand> WHEN ..." — text before the
    # first WHEN that is not itself a WHEN clause.
    case_operand = None
    head = re.split(r"(?i)\bwhen\b", body, maxsplit=1)
    if head and head[0].strip():
        case_operand = head[0].strip()

    branches: list[dict] = []
    # Each "WHEN ... THEN ..." up to the next WHEN / ELSE / end of body.
    for wm in re.finditer(r"(?is)\bwhen\b(.+?)\bthen\b(.+?)(?=\bwhen\b|\belse\b|$)", body):
        cond = wm.group(1).strip()
        result = wm.group(2).strip()
        clause = _parse_condition(cond, case_operand, cols)
        if not clause:
            return None  # a WHEN we can't model — hand the whole thing to AI
        clause["then"] = _sql_result_to_template(result)
        branches.append(clause)
    if not branches:
        return None

    default = ""
    em = re.search(r"(?is)\belse\b(.+?)$", body)
    if em:
        default = _sql_result_to_template(em.group(1).strip())

    return {
        "rule_type": "CASE_WHEN",
        "config": {"branches": branches, "default": default},
        "explanation": (f"Parsed a SQL CASE into {len(branches)} branch"
                        f"{'es' if len(branches) != 1 else ''}"
                        + (" plus a default" if default else "") + "."),
        "ambiguities": [],
        "source": "sql",
    }


def _sql_prompt(sql: str, cols: list[str], target_field: Optional[str]) -> str:
    cols_s = ", ".join(cols[:80]) if cols else "(unknown)"
    return (
        "Convert this SQL expression into ONE transformation rule for an Oracle "
        "data-conversion tool. Return ONLY JSON: "
        '{"rule_type": <TYPE>, "config": {...}, "explanation": "...", '
        '"ambiguities": ["..."]}.\n'
        f"Allowed rule_type values: {', '.join(_SUPPORTED_TYPES)}.\n"
        "A CASE expression -> CASE_WHEN with config "
        '{"branches":[{"if_column","op","value","then"}],"default"}. '
        "op is one of eq, neq, gt, gte, lt, lte, contains, startswith, endswith, "
        "isblank, notblank. In a THEN/ELSE result, reference another column as "
        "{Column_Name} (e.g. 'E' || Employee_ID becomes \"E{Employee_ID}\").\n"
        f"Source columns available: {cols_s}.\n"
        f"Target field: {target_field or '(unspecified)'}.\n"
        f"SQL:\n{sql.strip()}"
    )


async def _ai_translate_sql(sql: str, cols: list[str],
                            target_field: Optional[str]) -> Optional[dict]:
    from app.config import settings
    api_key = getattr(settings, "ANTHROPIC_API_KEY", None)
    if not api_key:
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": getattr(settings, "ANTHROPIC_MODEL", None) or "claude-sonnet-4-6",
                      "max_tokens": 1500,
                      "messages": [{"role": "user",
                                    "content": _sql_prompt(sql, cols, target_field)}]},
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", [])
                           if b.get("type") == "text")
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e == -1:
            return None
        obj = json.loads(text[s:e + 1])
        rt = (obj.get("rule_type") or "").strip().upper()
        if rt not in _SUPPORTED_TYPES:
            return None
        return {"rule_type": rt, "config": obj.get("config") or {},
                "explanation": obj.get("explanation") or "",
                "ambiguities": obj.get("ambiguities") or [], "source": "ai-sql"}
    except Exception as exc:  # noqa: BLE001
        log.warning("SQL translate AI failed: %s", exc)
        return None


async def translate_sql(conversion: Conversion, sql: str,
                        target_field_id: Optional[str] = None,
                        source_column: Optional[str] = None) -> dict:
    """SQL text -> {rule_type, config, explanation, ambiguities, source}.

    Deterministic CASE parse first (offline, no cost), Claude for anything else,
    and a benign editable CONSTANT if neither can — the box never errors.
    """
    cols = await _source_columns(conversion)
    target_field = None
    if target_field_id:
        try:
            from beanie import PydanticObjectId
            f = await FBDIField.get(PydanticObjectId(str(target_field_id)))
            target_field = f.field_name if f else None
        except Exception:  # noqa: BLE001
            target_field = None

    local = parse_sql_case(sql or "", cols)
    if local:
        return local
    ai = await _ai_translate_sql(sql or "", cols, target_field)
    if ai:
        return ai
    return {
        "rule_type": "CONSTANT", "config": {"value": ""},
        "explanation": ("Couldn't parse this SQL automatically (AI unavailable). "
                        "A CASE ... WHEN ... THEN ... END expression parses "
                        "offline; other SQL needs the AI path."),
        "ambiguities": [], "source": "local",
    }
