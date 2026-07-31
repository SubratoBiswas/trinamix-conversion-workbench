"""One rule typed in English, and every conversion it is supposed to reach.

Analyst, 31-Jul, pointing at the steer box on Mapping Review:

    "When I write a rule in english here, it should be converted to rule by AI and
     mapping should be updated across all the conversions (e.g. for supplier, 6
     conversions should be affected), as this is a global rule setter."
    "...should be saved in learning, change everywhere and affect existing, older
     conversions and future conversions."

There are THREE audiences for one sentence and they are reached by three different
mechanisms, which is why partial versions of this kept looking finished:

  the conversion in front of the analyst   ← written directly by the steer
  the conversions that already exist       ← the fan-out
  the conversions created next month       ← the learning library

The last one was the hole. `_learn` filed the rule under ONE business object — the
one whose screen the analyst happened to be on — and `apply_learned_to_conversion`
asks the library using ITS OWN object. So a Supplier load's other five conversions
were corrected today by the fan-out and the five created next month were not. Same
instruction, right for a week, silently wrong afterwards, and nothing on any screen
distinguishes the two cases. Now the library is written across the load sequence.

The second, quieter hole: `apply_learned_to_conversion` was the last reader still
comparing `target_object` with `==` while every other reader had been widened to
`object_keys_for_object`. It is also the reader that actually puts the mapping on
the row, so an exact match there meant a rule filed as "Supplier Address" never
reached a conversion whose object reads "Supplier_Address".

These are BEHAVIOURAL tests: they drive the real `apply_steer_prompt`,
`propagate_learning_to_open_conversions` and `apply_learned_to_conversion` against
an in-memory stand-in for the ODM, so deleting the wiring fails them. The existing
coverage in test_english_to_rule.py is a source-level seam check — it proves the
argument is passed, not that a sixth conversion changed.

No API key is set in the test environment, so the model parser returns [] and the
deterministic Python parser handles the line. That is the documented fallback
("all should be done using AI, or a python function whichever is available") and it
means this file also proves steering works with no network.

Pure: stdlib + the services under test. No database.
"""
import asyncio
import os
import sys
import types
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    raise AssertionError(f"{name} {detail}".strip())


# ── a very small stand-in for Beanie ─────────────────────────────────────────
class _Cmp:
    """Makes `Model.attr == value` evaluate to a query dict, as Beanie does."""

    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return {self.name: other}

    def __hash__(self):
        return hash(self.name)


def _match(item, query):
    for k, v in (query or {}).items():
        got = getattr(item, k, None)
        if isinstance(got, _Cmp):        # unset attribute → class descriptor
            got = None
        if isinstance(v, dict):
            if "$in" in v and got not in v["$in"]:
                return False
            if "$ne" in v and got == v["$ne"]:
                return False
            if "$exists" in v:
                return False
        elif got != v:
            return False
    return True


class Row:
    """A stored document. `.set()` is what every service uses to write."""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    async def set(self, patch):
        for k, v in (patch or {}).items():
            setattr(self, k, v)
        return self

    async def insert(self):
        return self

    def __getattr__(self, name):
        # A real Beanie document has every declared field, defaulted. Unset ones
        # read as None here so the services under test see a document, not a hole.
        if name.startswith("__"):
            raise AttributeError(name)
        return None

    def __repr__(self):
        return f"Row({self.__dict__})"


class _Q:
    def __init__(self, sel):
        self._sel = sel

    async def to_list(self):
        return list(self._sel)

    async def update(self, patch):
        n = 0
        for it in self._sel:
            for k, v in (patch.get("$set") or {}).items():
                setattr(it, k, v)
            n += 1
        return Row(modified_count=n)


def fake_model(name, items, query_fields=()):
    class Doc:
        def __init__(self, **kw):
            self.id = kw.pop("id", None) or f"{name.lower()}-{len(items) + 1}"
            for k, v in kw.items():
                setattr(self, k, v)

        async def insert(self):
            items.append(self)
            return self

        async def set(self, patch):
            for k, v in (patch or {}).items():
                setattr(self, k, v)
            return self

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        @staticmethod
        def _query(args):
            q = {}
            for a in args:
                if isinstance(a, dict):
                    q.update(a)
            return q

        @classmethod
        def find(cls, *args, **kw):
            q = cls._query(args)
            return _Q([i for i in items if _match(i, q)])

        @classmethod
        def find_all(cls, *a, **k):
            return _Q(list(items))

        @classmethod
        async def find_one(cls, *args, **kw):
            q = cls._query(args)
            for i in items:
                if _match(i, q):
                    return i
            return None

        @classmethod
        async def get(cls, oid):
            for i in items:
                if getattr(i, "id", None) == oid:
                    return i
            return None

    Doc.__name__ = name
    Doc._items = items
    for f in query_fields:
        setattr(Doc, f, _Cmp(f))
    return Doc


def _module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


# ── the world: one supplier load, six conversions ────────────────────────────
SUPPLIER_OBJECTS = [
    "Supplier Import", "Supplier Address", "Supplier Site",
    "Supplier Site Assignment", "Supplier Contacts", "Supplier Banks",
]
STEER = "Supplier Name should be mapped to Legal Name for all conversions"
OLD = datetime(2026, 7, 1)          # an earlier analyst decision
FUTURE = datetime(2030, 1, 1)       # a decision made after the steer


class World:
    """Builds the fakes and installs them over the two services under test."""

    def __init__(self, *, extra_conversions=(), person_dates=None):
        self.convs, self.tpls, self.fields, self.maps = [], [], [], []
        self.learned, self.profiles, self.outputs = [], [], []
        person_dates = person_dates or {}

        def add(obj, project, cid, dsid, *, field_name="Supplier Name",
                approved_by=None, approved_at=None):
            tid = f"tpl-{cid}"
            self.tpls.append(Row(id=tid, business_object=obj))
            fid = f"fld-{cid}"
            self.fields.append(Row(
                id=fid, template_id=tid, field_name=field_name, sheet_id=None))
            self.profiles.append(Row(
                dataset_id=dsid, column_name="Legal Name"))
            self.profiles.append(Row(
                dataset_id=dsid, column_name="Name"))
            self.convs.append(Row(
                id=cid, template_id=tid, target_object=obj, project_id=project,
                source_dataset_ids=[dsid], dataset_id=dsid))
            self.maps.append(Row(
                id=f"map-{cid}", conversion_id=cid, target_field_id=fid,
                source_column="Name", default_value=None, status="suggested",
                approved_by=approved_by, approved_at=approved_at,
                suggested_transformation=None, records_auto_fixed=0))
            self.outputs.append(Row(
                id=f"out-{cid}", conversion_id=cid, status="complete"))

        for i, obj in enumerate(SUPPLIER_OBJECTS, start=1):
            who, when = person_dates.get(obj, (None, None))
            add(obj, "proj-supplier", f"c{i}", f"ds{i}",
                approved_by=who, approved_at=when)
        for spec in extra_conversions:
            add(*spec)

        self.Conversion = fake_model("Conversion", self.convs,
                                     ("project_id", "target_object"))
        self.FBDITemplate = fake_model("FBDITemplate", self.tpls, ())
        self.FBDIField = fake_model("FBDIField", self.fields, ("template_id",))
        self.FBDISheet = fake_model("FBDISheet", [], ("template_id",))
        self.MappingSuggestion = fake_model(
            "MappingSuggestion", self.maps, ("conversion_id", "target_field_id"))
        self.LearnedMapping = fake_model(
            "LearnedMapping", self.learned,
            ("kind", "target_object", "target_field", "client_id"))
        self.DatasetColumnProfile = fake_model(
            "DatasetColumnProfile", self.profiles, ("dataset_id",))
        self.ConvertedOutput = fake_model("ConvertedOutput", self.outputs,
                                          ("conversion_id",))
        self._saved = None

    # -- install / restore -------------------------------------------------
    def __enter__(self):
        from app.services import learning_service as ls
        from app.services import steering_service as ss
        self._ls, self._ss = ls, ss
        self._saved = {
            "ls": {k: getattr(ls, k) for k in
                   ("Conversion", "FBDITemplate", "FBDIField", "MappingSuggestion",
                    "LearnedMapping", "DatasetColumnProfile",
                    "source_erp_for_conversion")},
            "ss": {k: getattr(ss, k) for k in
                   ("Conversion", "FBDITemplate", "FBDIField", "MappingSuggestion",
                    "LearnedMapping")},
            "mods": {k: sys.modules.get(k) for k in
                     ("app.models.fbdi", "app.models.output", "app.models.learned",
                      "app.models.dataset", "app.services.client_service")},
        }
        for mod in (ls, ss):
            mod.Conversion = self.Conversion
            mod.FBDITemplate = self.FBDITemplate
            mod.FBDIField = self.FBDIField
            mod.MappingSuggestion = self.MappingSuggestion
            mod.LearnedMapping = self.LearnedMapping
        ls.DatasetColumnProfile = self.DatasetColumnProfile

        async def _no_erp(_c):
            return None
        ls.source_erp_for_conversion = _no_erp

        async def _cid(_c):
            return None

        async def _scope(_c):
            return {}

        sys.modules["app.models.fbdi"] = _module(
            "app.models.fbdi", FBDIField=self.FBDIField, FBDISheet=self.FBDISheet,
            FBDITemplate=self.FBDITemplate)
        sys.modules["app.models.output"] = _module(
            "app.models.output", ConvertedOutput=self.ConvertedOutput)
        sys.modules["app.models.learned"] = _module(
            "app.models.learned", LearnedMapping=self.LearnedMapping)
        sys.modules["app.models.dataset"] = _module(
            "app.models.dataset", DatasetColumnProfile=self.DatasetColumnProfile)
        sys.modules["app.services.client_service"] = _module(
            "app.services.client_service",
            client_id_for_conversion=_cid, scope_query=_scope)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved["ls"].items():
            setattr(self._ls, k, v)
        for k, v in self._saved["ss"].items():
            setattr(self._ss, k, v)
        for k, v in self._saved["mods"].items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        return False

    # -- helpers -----------------------------------------------------------
    def conv(self, obj):
        return next(c for c in self.convs if c.target_object == obj)

    def mapping(self, obj):
        c = self.conv(obj)
        return next(m for m in self.maps if m.conversion_id == c.id)

    def sources(self):
        return {c.target_object: self.mapping(c.target_object).source_column
                for c in self.convs}


def steer(world, prompt=STEER, on="Supplier Import"):
    from app.services.steering_service import apply_steer_prompt
    return asyncio.run(apply_steer_prompt(world.conv(on), prompt, actor="analyst"))


# ── 1. the headline: six conversions, one sentence ───────────────────────────
def test_one_english_rule_reaches_all_six_supplier_conversions():
    """"e.g. for supplier, 6 conversions should be affected."

    The origin is written directly; the other five arrive through the fan-out. If
    `extra_object_keys` stops being passed, five of six revert to "Name" and this
    fails — which is the whole point of testing the outcome instead of the call.
    """
    with World() as w:
        res = steer(w)
        check("the English line was parsed into a mapping directive",
              res["applied"] and res["applied"][0].get("source") == "Legal Name",
              f"got {res['applied']!r} / unmatched={res['unmatched']!r}")
        got = w.sources()
        check("all six conversions now read Legal Name",
              all(v == "Legal Name" for v in got.values()), f"got {got}")
        check("and the fan-out reported the other five",
              res["propagated"]["conversions"] == 5,
              f"got {res['propagated']!r}")


def test_the_sentence_is_not_written_into_the_column_as_a_constant():
    """The destructive failure this parser ordering exists to prevent: the DEFAULT
    pattern reading "should be" and storing "mapped to legal name for all
    conversions" as a literal on every row."""
    with World() as w:
        steer(w)
        vals = {m.default_value for m in w.maps}
        check("no row carries a sentence as its default", vals == {None},
              f"got {vals!r}")


# ── 2. the library, which is what a FUTURE conversion asks ───────────────────
def test_the_rule_is_stored_for_every_object_in_the_load_sequence():
    """The gap. Written under one object, a rule is invisible to the five siblings
    created after today — the fan-out cannot reach a conversion that does not
    exist yet, and the library is the only thing that can."""
    with World() as w:
        steer(w)
        objs = {lm.target_object for lm in w.learned if lm.kind == "column_mapping"}
        check("one learning per supplier object", objs == set(SUPPLIER_OBJECTS),
              f"got {sorted(objs)}")
        check("each one names the column the analyst asked for",
              {lm.original_value for lm in w.learned} == {"Legal Name"},
              f"got {[lm.original_value for lm in w.learned]}")


def test_a_conversion_created_after_the_steer_inherits_it():
    """End to end for "future conversions": steer today, create a Supplier Banks
    conversion tomorrow, and the real apply pass must find the rule. Before the
    bundle write there was no Supplier Banks row in the library to find."""
    from app.services.learning_service import apply_learned_to_conversion
    with World() as w:
        steer(w)
        # Tomorrow: a brand-new Supplier Banks conversion, nothing mapped.
        tid = "tpl-new"
        w.tpls.append(Row(id=tid, business_object="Supplier Banks"))
        w.fields.append(Row(
            id="fld-new", template_id=tid, field_name="Supplier Name", sheet_id=None))
        w.profiles.append(Row(
            dataset_id="ds-new", column_name="Legal Name"))
        new = Row(
            id="c-new", template_id=tid, target_object="Supplier Banks",
            project_id="proj-later", source_dataset_ids=["ds-new"], dataset_id="ds-new")
        w.convs.append(new)
        m = Row(
            id="map-new", conversion_id="c-new", target_field_id="fld-new",
            source_column=None, default_value=None, status="suggested",
            approved_by=None, approved_at=None, suggested_transformation=None,
            records_auto_fixed=0)
        w.maps.append(m)

        n = asyncio.run(apply_learned_to_conversion(new, [m]))
        check("the new conversion picked the rule up", n == 1, f"applied {n}")
        check("and it points at Legal Name", m.source_column == "Legal Name",
              f"got {m.source_column!r}")


def test_the_apply_pass_matches_every_spelling_of_the_object():
    """`apply_learned_to_conversion` was the last reader using `==` on
    target_object. A conversion whose object reads "Supplier_Banks" must still
    find a rule filed as "Supplier Banks"."""
    from app.services.learning_service import apply_learned_to_conversion
    with World() as w:
        steer(w)
        tid = "tpl-us"
        w.tpls.append(Row(id=tid, business_object="Supplier_Banks"))
        w.fields.append(Row(
            id="fld-us", template_id=tid, field_name="Supplier Name", sheet_id=None))
        w.profiles.append(Row(
            dataset_id="ds-us", column_name="Legal Name"))
        conv = Row(
            id="c-us", template_id=tid, target_object="Supplier_Banks",
            project_id="proj-later", source_dataset_ids=["ds-us"], dataset_id="ds-us")
        m = Row(
            id="map-us", conversion_id="c-us", target_field_id="fld-us",
            source_column=None, default_value=None, status="suggested",
            approved_by=None, approved_at=None, suggested_transformation=None,
            records_auto_fixed=0)
        w.convs.append(conv)
        w.maps.append(m)
        asyncio.run(apply_learned_to_conversion(conv, [m]))
        check("underscore spelling still finds the rule",
              m.source_column == "Legal Name", f"got {m.source_column!r}")


# ── 3. existing and OLDER conversions ────────────────────────────────────────
def test_it_reaches_a_conversion_from_an_earlier_project():
    """"affect existing, older conversions". The fan-out walks every conversion,
    not the current project, so last month's Supplier Address load is corrected
    too — client and source scoping are what keep that from leaking."""
    with World(extra_conversions=[
            ("Supplier Address", "proj-june", "c-old", "ds-old")]) as w:
        steer(w)
        old = next(m for m in w.maps if m.conversion_id == "c-old")
        check("the older conversion was corrected",
              old.source_column == "Legal Name", f"got {old.source_column!r}")
        check("its generated output is marked stale",
              next(o for o in w.outputs if o.conversion_id == "c-old").status == "stale")


def test_an_unrelated_object_is_left_alone():
    """The bundle is this project's load sequence, not everything in the database.
    A Customer conversion must not be touched by a supplier instruction."""
    with World(extra_conversions=[
            ("Customer", "proj-cust", "c-cust", "ds-cust")]) as w:
        steer(w)
        cust = next(m for m in w.maps if m.conversion_id == "c-cust")
        check("Customer keeps its own mapping", cust.source_column == "Name",
              f"got {cust.source_column!r}")
        check("and no Customer learning was written",
              all(lm.target_object != "Customer" for lm in w.learned))


# ── 4. the date rule still arbitrates ────────────────────────────────────────
def test_an_earlier_human_decision_is_superseded():
    """"The last mapping with respect to date is final." A colleague's 1-Jul
    approval is an earlier statement of the same intent, so the steer wins."""
    with World(person_dates={"Supplier Site": ("priya", OLD)}) as w:
        steer(w)
        check("the older human decision is replaced",
              w.mapping("Supplier Site").source_column == "Legal Name",
              f"got {w.mapping('Supplier Site').source_column!r}")


def test_a_later_human_decision_stands():
    """The same rule in the other direction — whoever spoke last wins, and the
    steer must not silently undo a decision made after it."""
    with World(person_dates={"Supplier Contacts": ("priya", FUTURE)}) as w:
        res = steer(w)
        check("the newer human decision survives",
              w.mapping("Supplier Contacts").source_column == "Name",
              f"got {w.mapping('Supplier Contacts').source_column!r}")
        check("and the skip is reported rather than hidden",
              res["propagated"]["conversions"] == 4,
              f"got {res['propagated']!r}")


# ── 5. a column that does not exist is refused, not written ──────────────────
def test_a_column_absent_from_a_sibling_extract_is_not_forced_onto_it():
    """Propagation checks the extract. Pointing a mapping at a column the file
    does not have reads as mapped on screen and produces an empty column in the
    FBDI — the failure mode this tool exists to prevent."""
    with World() as w:
        # Supplier Banks is fed by a file with no Legal Name column.
        w.profiles = [p for p in w.profiles
                      if not (p.dataset_id == "ds6" and p.column_name == "Legal Name")]
        w.DatasetColumnProfile._items[:] = w.profiles
        steer(w)
        check("the sibling without the column is skipped",
              w.mapping("Supplier Banks").source_column == "Name",
              f"got {w.mapping('Supplier Banks').source_column!r}")
        check("the ones that do have it are still corrected",
              w.mapping("Supplier Address").source_column == "Legal Name")


# ── 6. removal is a decision too ─────────────────────────────────────────────
def test_leave_blank_propagates_as_a_suppression():
    """"...if the user wants to change any mapping, REMOVE etc." A suppression has
    to travel the same road, and set the row not_applicable with the source column
    cleared rather than approving it in place."""
    with World() as w:
        res = steer(w, prompt="leave Supplier Name blank")
        check("it was parsed as a suppression",
              res["applied"] and res["applied"][0].get("suppressed") is True,
              f"got {res['applied']!r} unmatched={res['unmatched']!r}")
        bad = {o: (m.status, m.source_column) for o in SUPPLIER_OBJECTS
               for m in [w.mapping(o)]
               if not (m.status == "not_applicable" and m.source_column is None)}
        check("every supplier conversion is blanked", not bad, f"got {bad}")
        check("and it is stored for every object in the sequence",
              {lm.target_object for lm in w.learned if lm.kind == "suppress_field"}
              == set(SUPPLIER_OBJECTS))


def _all():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    for name, fn in _all():
        print(name)
        fn()
    print(f"\n{len(_all())} tests passed")
