"""Why "Defaulted -> 900001" would not go away.

Analyst, 30-Jul, with a screenshot of Batch ID: "this is still defaulted to 900001,
why not able to fix this".

By then Batch ID was blanked in three places — the 30-Jul corrections file, a
suppress_field learning, and the strategy overlay's blank set — and the generated
FBDI shipped it empty. The screen still said 900001. Two layers were reporting a
value nothing would write:

  1. ``compute_effective_defaults`` — the endpoint the Mapping Review screen reads —
     had NO suppression check at all. It skipped a field only when the field had a
     source column (``if m and m.source_column: continue``), so an unmapped, blanked
     Batch ID fell straight through to ``elif norm in _CONTROL_DEFAULTS`` and came
     back as 900001.

  2. The page carried its OWN hard-coded copy of the backend's control-defaults
     table, checked BEFORE the server's answer. No rule, learning, correction or
     Keep blank press could reach a constant compiled into the browser bundle. That
     copy had also drifted: Tax Organization Type = "Corporation" and Business
     Relationship = "PROSPECTIVE", both corrected on the server weeks earlier.

The first three tests drive the real ``compute_effective_defaults`` with fake
models, so they fail if the suppression check is removed or reordered. The last two
are seams over the frontend, because a backend fix alone leaves the number on screen.

Pure: stdlib + the service under test. No database.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    raise AssertionError(f"{name} {detail}".strip())


# ── fakes ────────────────────────────────────────────────────────────────────
class F:
    """An FBDIField."""

    def __init__(self, fid, name, required=False):
        self.id, self.field_name, self.required = fid, name, required


class M:
    """A MappingSuggestion."""

    def __init__(self, fid, status="suggested", source_column=None, default_value=None):
        self.target_field_id, self.status = fid, status
        self.source_column, self.default_value = source_column, default_value


class C:
    """A Conversion."""

    def __init__(self, target_object="Supplier"):
        self.id, self.template_id = "conv1", "tpl1"
        self.target_object = target_object


class _Q:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)

    def __aiter__(self):
        async def gen():
            for i in self._items:
                yield i
        return gen()


def _fake_model(items):
    """A stand-in whose .find(...) ignores the query and returns `items`."""
    ns = types.SimpleNamespace()
    ns.find = lambda *a, **k: _Q(items)
    # Beanie query expressions (Model.field == value) are built off class
    # attributes; any object works because the fake .find drops its arguments.
    for attr in ("template_id", "conversion_id", "kind", "target_object"):
        setattr(ns, attr, object())
    return ns


class L:
    """A LearnedMapping."""

    def __init__(self, target_field, kind="suppress_field", resolved_value=""):
        self.target_field, self.kind = target_field, kind
        self.resolved_value = resolved_value


def _stub_client_service():
    """Stand in for the client-scoping helpers, which need a database.

    Without this the learnings branch raises, is swallowed by its except, and the
    suppress_field path silently goes untested — which is the same shape of hole
    this whole file exists to close.
    """
    mod = types.ModuleType("app.services.client_service")

    async def client_id_for_conversion(_c):
        return None

    async def scope_query(_cid):
        return {}

    mod.client_id_for_conversion = client_id_for_conversion
    mod.scope_query = scope_query
    return mod


def run(fields, maps, learnings=(), target_object="Supplier"):
    """Call the REAL compute_effective_defaults against the fakes."""
    from app.services import defaults_service as ds
    old = (ds.FBDIField, ds.MappingSuggestion, ds.LearnedMapping)
    old_cs = sys.modules.get("app.services.client_service")
    ds.FBDIField = _fake_model(fields)
    ds.MappingSuggestion = _fake_model(maps)
    ds.LearnedMapping = _fake_model(learnings)
    sys.modules["app.services.client_service"] = _stub_client_service()
    try:
        return asyncio.run(
            ds.compute_effective_defaults(C(target_object), use_ai=False))
    finally:
        ds.FBDIField, ds.MappingSuggestion, ds.LearnedMapping = old
        if old_cs is not None:
            sys.modules["app.services.client_service"] = old_cs
        else:
            sys.modules.pop("app.services.client_service", None)


# ── tests ────────────────────────────────────────────────────────────────────
def test_a_control_default_still_fills_a_field_nobody_blanked():
    """The control is the control: this must keep working, or the fix is a regression."""
    r = run([F(1, "Import Action")], [M(1)])
    check("Import Action is still defaulted",
          r["defaults"].get("import action") == "CREATE",
          f"got {r['defaults']!r}")


def test_batch_id_is_not_reported_as_defaulted():
    """The reported symptom. Batch ID is blanked by the 30-Jul corrections, which
    feed the strategy overlay's blank set for Supplier."""
    from app.services.strategy_overlay import blank_fields
    check("the overlay does say Batch ID is blank for Supplier",
          "batch id" in {str(x).lower() for x in blank_fields("Supplier")},
          "the corrections file no longer feeds the overlay")

    r = run([F(1, "Batch ID"), F(2, "Import Action")], [M(1), M(2)])
    check("Batch ID is not in defaults", "batch id" not in r["defaults"],
          f"got {r['defaults'].get('batch id')!r}")
    check("Batch ID is reported as suppressed", "batch id" in r["suppressed"])
    check("and it is not in the detail list either",
          all(d["field"] != "batch id" for d in r["detail"]))
    check("the unrelated control default is untouched",
          r["defaults"].get("import action") == "CREATE")


def test_a_stale_default_on_the_row_cannot_resurrect_a_blanked_field():
    """The suppression check runs BEFORE the m.default_value branch on purpose.

    A default left on the mapping row by an earlier seed is exactly what kept
    re-appearing after the analyst blanked the field — the row outranked the
    decision.
    """
    r = run([F(1, "Batch ID")], [M(1, status="approved", default_value="900001")])
    check("the stale row default does not win", "batch id" not in r["defaults"],
          f"got {r['defaults'].get('batch id')!r}")


def test_keep_blank_alone_is_enough_to_suppress():
    """No strategy rule, no learning — just a not_applicable mapping."""
    r = run([F(1, "Some Odd Field")], [M(1, status="not_applicable")])
    check("Keep blank suppresses the field",
          "some odd field" in r["suppressed"])

    # ...but not_applicable WITH an explicit default means "populate with this
    # constant" (Invoice Match Option = Receipt), which must survive.
    r2 = run([F(1, "Invoice Match Option")],
             [M(1, status="not_applicable", default_value="Receipt")])
    check("an explicit default is still intent to populate",
          r2["defaults"].get("invoice match option") == "Receipt",
          f"got {r2['defaults']!r}")
    check("and it is not listed as suppressed",
          "invoice match option" not in r2["suppressed"])


def test_a_suppress_field_learning_alone_is_enough():
    """This is how Keep blank on ONE conversion reaches all the others: the press
    writes a suppress_field learning, and every conversion of the object reads it.
    If this path did not suppress, propagation would look like it worked while each
    other conversion kept showing the old constant."""
    r = run([F(1, "Batch ID"), F(2, "Tax Reporting Name"), F(3, "Import Action")],
            [M(1), M(2), M(3)],
            learnings=[L("Import Action")])
    check("the learning suppresses Import Action",
          "import action" in r["suppressed"])
    check("so it is no longer reported as defaulted",
          "import action" not in r["defaults"], f"got {r['defaults']!r}")


def test_a_mapped_field_is_never_called_a_default():
    r = run([F(1, "Supplier Name")], [M(1, source_column="vendorname")])
    check("mapped fields stay out of defaults", not r["defaults"])


def test_the_frontend_has_no_private_copy_of_the_defaults_table():
    """Seam. The backend fix is invisible while the browser answers from a constant."""
    page = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src",
                        "pages", "MappingReviewPage.tsx")
    src = open(page, encoding="utf-8").read()

    check("no hard-coded control-defaults table", "const CONTROL_DEFAULTS" not in src)
    check("no controlDefaultFor() lookup", "controlDefaultFor" not in src)
    # The specific constants that were wrong on screen.
    for gone in ('"batch id": "900001"', '"tax organization type": "Corporation"',
                 '"business relationship": "PROSPECTIVE"'):
        check(f"the browser no longer carries {gone}", gone not in src)

    check("defaults come from the server's answer", "effectiveDefaults[k]" in src)
    check("the page reads the suppressed list", "setSuppressedFields" in src)
    check("and a suppressed field beats a stored default_value",
          "if (suppressed?.has(k)) return \"\";" in src)


def test_the_endpoint_still_returns_what_the_frontend_reads():
    """Seam over the contract between the two halves of this fix."""
    here = os.path.dirname(__file__)
    svc = open(os.path.join(here, "..", "app", "services", "defaults_service.py"),
               encoding="utf-8").read()
    body = svc.split("async def compute_effective_defaults(")[1]

    check("the service returns the suppressed list", '"suppressed": sorted(suppressed)' in body)
    # Order matters: suppression must be tested before any value source is read.
    i_check = body.index("if norm in suppressed:")
    i_dv = body.index("if m and m.default_value:")
    check("suppression is checked before the row's default", i_check < i_dv)

    api = open(os.path.join(here, "..", "..", "frontend", "src", "api", "index.ts"),
               encoding="utf-8").read()
    check("the API type declares suppressed", "suppressed?: string[];" in api)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__)
        fn()
    print("\nall effective-default suppression checks passed")
