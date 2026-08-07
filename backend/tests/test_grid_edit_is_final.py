"""Any grid edit is FINAL — no Approve click, and the library can't quietly undo it.

Subrato, 06-Aug: "user should not click on approve, any edit should be considered
final and applied to the store (learnings) and applied to existing and new
conversions". The reliability half of that is the important half: a mapping row left
at status "suggested" is fair game for apply_learned_to_conversion, which overwrites
it from the library on the next generate/refresh — the "I saved it, refreshed, it's
gone" complaint. Turning every content edit into a DECIDED, person-attributed row is
what protects it.

Two layers are tested:
  * finalize_content_edit — the pure status decision (overridden vs decided-blank),
    and that it stays out of the way when the UI sent an explicit status.
  * the store — a person-decided row becomes a dated statement that beats an OLDER
    library entry, while an un-decided "suggested" row does not, so the library wins
    and the value the analyst saw vanishes. That contrast is the whole bug.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mapping_edit import finalize_content_edit          # noqa: E402
from app.services import mapping_store as store                      # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


# ── the pure status decision ────────────────────────────────────────────────

def test_setting_a_source_column_is_a_final_override():
    out = finalize_content_edit({"source_column": "internalid"},
                                cur_source=None, cur_default=None, cur_transform=None)
    check("a source edit becomes overridden", out.get("status") == "overridden", out)


def test_typing_a_fixed_value_is_a_final_override():
    out = finalize_content_edit({"default_value": "US"},
                                cur_source=None, cur_default=None, cur_transform=None)
    check("a default edit becomes overridden", out.get("status") == "overridden", out)


def test_authoring_a_rule_is_a_final_override():
    out = finalize_content_edit({"suggested_transformation": {"rule_type": "CONCAT"}},
                                cur_source=None, cur_default=None, cur_transform=None)
    check("a rule edit becomes overridden", out.get("status") == "overridden", out)


def test_clearing_everything_is_a_decided_keep_blank():
    # Row currently has a source; the edit clears it and adds no default.
    out = finalize_content_edit({"source_column": None},
                                cur_source="entityid", cur_default=None, cur_transform=None)
    check("clearing becomes not_applicable", out.get("status") == "not_applicable", out)
    check("source is nulled", out.get("source_column") is None)
    check("default is nulled", out.get("default_value") is None)
    check("nothing left to review", out.get("review_required") == 0)


def test_an_explicit_status_is_left_untouched():
    # Approve / Reject / override send their own status — the helper must not fight it.
    check("explicit approved is respected",
          finalize_content_edit({"source_column": "x", "status": "approved"},
                                cur_source=None, cur_default=None, cur_transform=None) == {})
    check("explicit suggested (unmap) is respected",
          finalize_content_edit({"source_column": None, "status": "suggested"},
                                cur_source="x", cur_default=None, cur_transform=None) == {})


def test_a_non_content_edit_is_not_a_decision():
    check("editing only a comment decides nothing",
          finalize_content_edit({"comment": "note"},
                                cur_source="x", cur_default=None, cur_transform=None) == {})


def test_keeping_a_source_while_clearing_the_default_stays_a_mapping():
    out = finalize_content_edit({"default_value": ""},
                                cur_source="entityid", cur_default="OLD", cur_transform=None)
    check("still a positive mapping via the untouched source",
          out.get("status") == "overridden", out)


# ── the store: a final edit beats an older library value; a suggested one does not ─

class _Row:
    """A LearnedMapping stand-in for entries_of/resolve."""
    def __init__(self, **k):
        for a in ("kind", "target_field", "original_value", "resolved_value",
                  "client_id", "source_erp", "effective_date", "captured_at",
                  "captured_from", "captured_by", "rule_type", "rule_config",
                  "sheets", "exclude_sheets", "is_deleted", "target_object"):
            setattr(self, a, k.get(a))
        self.sheets = self.sheets or []
        self.exclude_sheets = self.exclude_sheets or []
        self.is_deleted = bool(self.is_deleted)


class _Mapping:
    """A MappingSuggestion stand-in for entry_from_mapping."""
    def __init__(self, **k):
        for a in ("approved_by", "status", "source_column", "default_value",
                  "suggested_transformation", "approved_at"):
            setattr(self, a, k.get(a))


_FIELD = "Party Original System Reference"


def _library_says_entityid():
    """The library's older statement: this field maps from entityid (05-Aug 10:00)."""
    return _Row(kind="column_mapping", target_field=_FIELD, original_value="entityid",
                client_id="c1", source_erp="netsuite",
                effective_date=datetime(2026, 8, 5, 10, 0))


def test_a_final_edit_beats_an_older_library_value():
    """The analyst re-maps the field to internalid and it is saved as a final
    (overridden, person-stamped) decision dated later — the store must pick it."""
    lib = _library_says_entityid()
    edit = _Mapping(approved_by="ana@nextpower.com", status="overridden",
                    source_column="internalid",
                    approved_at=datetime(2026, 8, 6, 14, 0))
    grid_entry = store.entry_from_mapping(edit, target_field=_FIELD,
                                          client_id="c1", source_erp="netsuite")
    check("a final edit IS a dated statement", grid_entry is not None)
    winner = store.resolve(store.entries_of([lib]) + [grid_entry],
                           target_field=_FIELD, client_id="c1", source_erp="netsuite")
    check("the analyst's later edit wins", winner.value == "internalid",
          f"got {winner.value!r}")


def test_an_undecided_suggested_edit_is_not_protected():
    """The bug, pinned: an edit left at 'suggested' with no approver is NOT a
    statement, so only the older library value remains and the field reverts to
    entityid — exactly 'I saved it, refreshed, it's gone'. This is what the final-
    edit change prevents."""
    lib = _library_says_entityid()
    unsaved = _Mapping(approved_by=None, status="suggested",
                       source_column="internalid", approved_at=None)
    grid_entry = store.entry_from_mapping(unsaved, target_field=_FIELD,
                                          client_id="c1", source_erp="netsuite")
    check("a suggested, unapproved edit is NOT a protected statement",
          grid_entry is None)
    winner = store.resolve(store.entries_of([lib]),
                           target_field=_FIELD, client_id="c1", source_erp="netsuite")
    check("so the OLD library value is what survives", winner.value == "entityid",
          f"got {winner.value!r}")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\ngrid edits are final and protected from the library")
