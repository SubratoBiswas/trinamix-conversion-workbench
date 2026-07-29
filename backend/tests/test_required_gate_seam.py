"""The two seams the 29-Jul live run exposed, both invisible to unit tests.

The required-field logic itself was already covered (test_required_fields, 17
checks) — and passed. What was broken was the WIRING on either side of it:

  A. ``required-check`` handed ``check_sheets`` a dict keyed by DATASET ID, because
     that is what ``build_converted_dataframe(collect_frames=...)`` populates. No
     required sheet name could match, so every field read as "missing" and the gate
     returned ``blocked: true`` on a healthy Supplier conversion. A gate that fires
     100% of the time is worse than no gate — the analyst switches it off.

  B. ``mapping-report`` called the ENDPOINT FUNCTION ``required_check`` directly, so
     ``max_rows`` defaulted to a FastAPI ``Query(...)`` object rather than an int.
     The call raised, a bare ``except`` swallowed it, and the report's required-field
     section read zero on every conversion. ``include_required=true`` and ``=false``
     returned byte-identical output.

Both are seam defects: each side is correct alone and wrong together. So these
tests assert the CONTRACT of the seam, not the arithmetic behind it.

Pure: pandas + stdlib. No DB, no network, no FastAPI app.
"""
import ast
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import required_fields_service as rf  # noqa: E402
from app.services.output_service import route_frame  # noqa: E402

_BACKEND = Path(__file__).resolve().parent.parent
_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


# ── A. The regression itself ─────────────────────────────────────────────────
_REQUIRED = {
    "Supplier Import": ["Supplier Name"],
    "Supplier Address Import": ["Supplier Name", "Address Name"],
}


def test_dataset_id_keys_report_everything_missing():
    """Reproduces the live failure so the fix has something to be a fix OF.

    Frames keyed by dataset id — exactly what the router used to pass.
    """
    frames = {
        "6a69d622a3bcef37a26905d8": pd.DataFrame({"Supplier Name": ["ACME"],
                                                  "Address Name": ["HQ"]}),
    }
    res = rf.check_sheets(frames, _REQUIRED)
    check("blocked (the live symptom)", res["blocked"] is True)
    check("every sheet reported as not generated",
          all(not s["sheet_generated"] for s in res["sheets"]))
    check("all 3 required fields reported missing", res["failed_count"] == 3,
          f"got {res['failed_count']}")


def test_sheet_named_keys_pass_the_same_data():
    """Same values, keyed by interface sheet name — the gate must now be quiet.

    This is the whole fix in one assertion: nothing about the DATA changed.
    """
    df = pd.DataFrame({"Supplier Name": ["ACME"], "Address Name": ["HQ"]})
    res = rf.check_sheets({"Supplier Import": df,
                           "Supplier Address Import": df}, _REQUIRED)
    check("not blocked", res["blocked"] is False, f"got {res}")
    check("both sheets generated",
          all(s["sheet_generated"] for s in res["sheets"]))
    check("no failures", res["failed_count"] == 0)


def test_a_real_gap_still_blocks():
    """The fix must not turn the gate into a rubber stamp."""
    res = rf.check_sheets(
        {"Supplier Import": pd.DataFrame({"Supplier Name": ["ACME"]}),
         "Supplier Address Import": pd.DataFrame({"Supplier Name": ["ACME", "ACME"],
                                                  "Address Name": ["", ""]})},
        _REQUIRED)
    check("blocked", res["blocked"] is True)
    check("names the empty field",
          res["failures"] == [{"sheet": "Supplier Address Import",
                               "field": "Address Name"}], f"got {res['failures']}")


# ── The routing helper generation and the gate now share ─────────────────────
def _frames():
    party = pd.DataFrame({"Party Name": ["ACME"]})
    addr = pd.DataFrame({"Address Line 1": ["1 Main St"] * 3})
    return {
        "ds_party": (party, ["name", "taxid"]),
        "ds_addr": (addr, ["address1", "city", "postal"]),
    }, party, addr


def test_route_frame_picks_the_file_that_has_the_columns():
    src, party, addr = _frames()
    merged = pd.DataFrame({"x": [1]})
    check("address columns -> address file",
          route_frame({"address1", "city"}, src, merged) is addr)
    check("party columns -> party file",
          route_frame({"name"}, src, merged) is party)


def test_route_frame_falls_back_to_the_merged_frame():
    src, _p, _a = _frames()
    merged = pd.DataFrame({"x": [1]})
    check("no evidence -> merged", route_frame({"nothing_matches"}, src, merged)
          is merged)
    check("nothing wanted -> merged", route_frame(set(), src, merged) is merged)
    check("single source -> merged", route_frame({"address1"}, {}, merged) is merged)
    check("None wanted -> merged", route_frame(None, src, merged) is merged)


def test_route_frame_ignores_case_and_padding():
    """Source headers arrive with stray case and spacing; a miss here would send a
    sheet to the wrong file, which is silent and looks like bad data."""
    src, _p, addr = _frames()
    merged = pd.DataFrame({"x": [1]})
    check("' Address1 ' still routes",
          route_frame({" Address1 "}, src, merged) is addr)


def test_route_frame_tolerates_a_bare_frame_entry():
    """Defensive: a caller that stored frames without the column list must degrade
    to the fallback, not raise mid-generation."""
    merged = pd.DataFrame({"x": [1]})
    out = route_frame({"a"}, {"ds": pd.DataFrame({"a": [1]})}, merged)
    check("no crash, falls back", out is merged)


# ── B. The endpoint-called-as-a-function seam ────────────────────────────────
def _ops_tree():
    return ast.parse((_BACKEND / "app" / "routers" / "operations.py").read_text())


def _fastapi_default_endpoints(tree):
    """Endpoint names whose signature carries a FastAPI Query/Body/Depends default."""
    out = {}
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                   and d.func.attr in ("get", "post", "put", "patch", "delete")
                   for d in n.decorator_list):
            continue
        bad = [a for a in (n.args.defaults + n.args.kw_defaults)
               if isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
               and a.func.id in ("Query", "Body", "Depends", "Path", "Form", "File")]
        if bad:
            out[n.name] = len(bad)
    return out


def test_no_endpoint_is_called_as_a_plain_function():
    """The general form of bug B, enforced across the whole router module.

    An endpoint's defaults are FastAPI descriptors, not values. Calling one from
    Python passes those descriptors through as data — which fails deep inside
    pandas, far from the call, and is easy to swallow.
    """
    tree = _ops_tree()
    eps = _fastapi_default_endpoints(tree)
    # Names shadowed by a function-local import of a same-named service resolve to
    # the service, not the endpoint. That is fragile but not this bug; the alias in
    # ai_explain_load_errors removes the one instance.
    local_imports = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                local_imports.add(a.asname or a.name.split(".")[0])
    offenders = [f"{n.func.id}() at line {n.lineno}" for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id in eps and n.func.id not in local_imports]
    check("no direct endpoint calls", not offenders, f"got {offenders}")


def test_the_gate_helper_exists_and_takes_a_real_int():
    """``run_required_check`` is the callable-from-Python form. If someone deletes
    it and points mapping-report back at the endpoint, bug B returns."""
    tree = _ops_tree()
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "run_required_check"), None)
    check("run_required_check defined", fn is not None)
    check("it is not an endpoint", not fn.decorator_list)
    defaults = fn.args.defaults
    check("max_rows defaults to a plain literal int",
          len(defaults) == 1 and isinstance(defaults[0], ast.Constant)
          and isinstance(defaults[0].value, int), f"got {defaults}")


def test_mapping_report_uses_the_helper_and_records_the_error():
    """A swallowed exception that reports a clean pass is the failure mode that hid
    bug B for weeks, so the handler must keep the reason."""
    src = (_BACKEND / "app" / "routers" / "operations.py").read_text()
    tree = _ops_tree()
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "mapping_report")
    calls = {n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("calls run_required_check", "run_required_check" in calls)
    check("does not call the endpoint", "required_check" not in calls)
    handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
    named = [h for h in handlers if h.name]
    check("the gate's except binds the exception", named,
          "a bare `except Exception:` cannot report why")
    check("the error reaches the payload",
          'rep["required_fields"]["error"] = req_error' in src)


# ── The gate's own semantics, unchanged by the fix ───────────────────────────
def test_partial_reports_without_blocking():
    """Some Oracle child rows are legitimately optional; blocking on those is how a
    gate gets switched off. Regression guard on the deliberate PARTIAL carve-out."""
    res = rf.check_sheets(
        {"Supplier Import": pd.DataFrame({"Supplier Name": ["ACME", ""]})},
        {"Supplier Import": ["Supplier Name"]})
    check("not blocked", res["blocked"] is False)
    check("still reported", res["partial_count"] == 1)


def test_a_field_satisfied_only_by_a_control_default_passes():
    """build_sheet_frames applies control defaults before checking, so a required
    field with no mapping at all but a curated constant must read as satisfied —
    the third of the three cases the gate's docstring promises."""
    res = rf.check_sheets(
        {"Supplier Import": pd.DataFrame({"Supplier Name": ["ACME"],
                                          "Supplier Type": ["STANDARD"]})},
        {"Supplier Import": ["Supplier Name", "Supplier Type"]})
    check("not blocked", res["blocked"] is False)


def test_build_sheet_frames_defaults_before_checking():
    """Static guard: the helper must run control defaults, else the case above only
    passes because the test frame was pre-filled by hand."""
    src = (_BACKEND / "app" / "services" / "output_service.py").read_text()
    fn = src.split("async def build_sheet_frames", 1)[1].split("\n# Coded-value")[0]
    for needed in ("_apply_control_defaults", "route_frame", "_blank_null_sentinels"):
        check(f"build_sheet_frames uses {needed}", needed in fn)
    check("keys are sheet names, not dataset ids",
          "out[str(s.sheet_name" in fn, "must key the result by sheet_name")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except AssertionError:
            pass
    print(f"\n{'=' * 60}")
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("all checks passed")
