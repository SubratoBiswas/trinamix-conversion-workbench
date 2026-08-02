"""A generated key is generated once, and repeats forever after.

Analyst, 31-Jul, on Party Number (CW row 23): "write a logic in which this should be
generated once and next time onwards the same should be repeated, not different number
each run."

The obvious implementation — a counter that walks the frame — satisfies "unique" and
"increments" and is still wrong, because it is a function of POSITION. The extract gets
re-sorted, filtered and re-uploaded constantly, and each of those renumbers everybody.
The damage is not cosmetic: Party Number is the key every child interface points at, so
a second load creates duplicate parties instead of updating the first, and two
downloads of "the same" data reconcile against each other on nothing.

So the number is a function of a NATURAL KEY from the source. These tests are the
guarantee, stated as the four ways it could break:

    same run again        → identical numbers
    rows re-ordered       → identical numbers
    rows added            → existing numbers untouched, new keys take fresh ones
    rows removed          → the freed number is NOT handed to someone else

Pure: stdlib. Drives the real allocator against an in-memory collection.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
        return
    raise AssertionError(f"{name} {detail}".strip())


class _Row:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)

    async def insert(self):
        _Store.rows.append(self)
        return self

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return None


class _Store:
    rows: list = []


def _match(r, q):
    for k, v in q.items():
        if getattr(r, k, None) != v:
            return False
    return True


class _Alloc(_Row):
    """Stand-in for SequenceAllocation. The unique index is enforced here too, so the
    race-handling path is exercised rather than assumed."""

    def __init__(self, **kw):
        super().__init__(**kw)

    async def insert(self):
        q = {k: getattr(self, k) for k in
             ("client_id", "target_object", "target_field", "natural_key")}
        if any(_match(r, q) for r in _Store.rows):
            raise RuntimeError("duplicate key")
        _Store.rows.append(self)
        return self

    @staticmethod
    def _q(args):
        q = {}
        for a in args:
            if isinstance(a, dict):
                q.update(a)
        return q

    @classmethod
    def find(cls, *args, **kw):
        sel = [r for r in _Store.rows if _match(r, cls._q(args))]

        class _Q:
            def __init__(self, items):
                self.items = items

            def sort(self, spec):
                if spec == "-seq":
                    self.items = sorted(self.items, key=lambda r: int(r.seq or 0),
                                        reverse=True)
                return self

            def limit(self, n):
                self.items = self.items[:n]
                return self

            async def to_list(self):
                return list(self.items)
        return _Q(sel)

    @classmethod
    async def find_one(cls, *args, **kw):
        q = cls._q(args)
        return next((r for r in _Store.rows if _match(r, q)), None)


class _Cmp:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return {self.name: other}

    def __hash__(self):
        return hash(self.name)


for _f in ("client_id", "target_object", "target_field", "natural_key",
           "parent_key", "seq"):
    setattr(_Alloc, _f, _Cmp(_f))


def _svc():
    from app.services import sequence_service as ss
    ss.SequenceAllocation = _Alloc
    return ss


def run(keys, parents=None):
    ss = _svc()
    return asyncio.run(ss.allocate_many(
        client_id="nextpower", target_object="Customer",
        target_field="Party Number", keys=keys, parent_keys=parents))


def fresh():
    _Store.rows = []


# ── the guarantee ───────────────────────────────────────────────────────────
def test_a_second_run_returns_the_same_numbers():
    fresh()
    first = run(["1001", "1002", "1003"])
    second = run(["1001", "1002", "1003"])
    check("the numbers are stable", first == second, f"{first} vs {second}")
    check("and they are the documented format",
          first["1001"] == "NXT000001" and first["1003"] == "NXT000003", f"{first}")


def test_reordering_the_extract_changes_nothing():
    """The failure a position-based counter cannot avoid. Sorting the file by name
    instead of id would renumber every customer."""
    fresh()
    first = run(["1001", "1002", "1003"])
    shuffled = run(["1003", "1001", "1002"])
    check("each key kept its own number",
          all(shuffled[k] == first[k] for k in first), f"{first} vs {shuffled}")


def test_new_rows_take_fresh_numbers_and_leave_the_others_alone():
    fresh()
    first = run(["1001", "1002"])
    later = run(["1001", "1002", "2001", "2002"])
    check("existing keys are untouched",
          later["1001"] == first["1001"] and later["1002"] == first["1002"],
          f"{first} vs {later}")
    check("new keys continue the sequence",
          later["2001"] == "NXT000003" and later["2002"] == "NXT000004", f"{later}")


def test_a_removed_row_does_not_free_its_number_for_someone_else():
    """The number is already sitting in a loaded Oracle record. Re-issuing it to a
    different entity is worse than skipping it — hence MAX(seq)+1, not count+1."""
    fresh()
    run(["1001", "1002", "1003"])
    _Store.rows = [r for r in _Store.rows if r.natural_key != "1002"]
    after = run(["1001", "1003", "9009"])
    check("the new key does not reuse 000002", after["9009"] == "NXT000004",
          f"got {after['9009']}")


def test_whitespace_and_case_do_not_split_one_entity_in_two():
    fresh()
    a = run(["  1001 "])
    b = run(["1001"])
    check("same entity, same number", a["1001"] == b["1001"], f"{a} vs {b}")
    check("and only one allocation exists", len(_Store.rows) == 1,
          f"got {len(_Store.rows)}")


# ── the PERSON case ─────────────────────────────────────────────────────────
def test_a_person_is_numbered_under_its_organisation():
    """The analyst's second format: NXT000001_C1. A person is numbered RELATIVE to
    its organisation, so the child counter is per parent, not global."""
    fresh()
    got = run(["ORG1", "P1", "P2", "ORG2", "P3"],
              parents={"p1": "ORG1", "p2": "ORG1", "p3": "ORG2"})
    check("the organisations take plain numbers",
          got["org1"] == "NXT000001" and got["org2"] == "NXT000002", f"{got}")
    check("the first org's people hang off it",
          got["p1"] == "NXT000001_C1" and got["p2"] == "NXT000001_C2", f"{got}")
    check("and the second org's counter starts again at 1",
          got["p3"] == "NXT000002_C1", f"{got}")


def test_people_are_stable_across_runs_too():
    fresh()
    parents = {"p1": "ORG1", "p2": "ORG1"}
    first = run(["ORG1", "P1", "P2"], parents=parents)
    second = run(["P2", "ORG1", "P1"], parents=parents)
    check("children keep their numbers", all(second[k] == first[k] for k in first),
          f"{first} vs {second}")


def _all():
    return [(n, f) for n, f in sorted(globals().items())
            if n.startswith("test_") and callable(f)]


if __name__ == "__main__":
    for name, fn in _all():
        print(name)
        fn()
    print(f"\n{len(_all())} tests passed")
