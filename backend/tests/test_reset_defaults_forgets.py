"""A targeted reset-defaults must forget the learning that feeds the field, whatever
its provenance — or the removal is undone at the next generate.

THE LIVE BUG (06-Aug, Customer). The analyst removed the Insert Update Indicator
default on most sheets; the screen showed them blank ("Gold-derived default removed
by user"), and the very next generated file still carried "I" on every one of those
rows. The default had been captured from a SIBLING conversion
("Customer_Shipping_Address -> Customer Import"), so its captured_from was neither
"gold example" nor "ai-inference". reset-defaults filtered on exactly those two
sources, so it never forgot this learning — and apply_learned re-applied it on every
regenerate. Clearing looked done and the store put it straight back.

The fix: when the analyst NAMES a field, forget whatever example_default feeds it,
regardless of where it was captured. A blanket reset (no field named) stays
conservative — gold / AI only, never a human-authored learning.

Pure: the decision is a module-level helper, tested here without a database.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.routers.conversions import _should_forget_default  # noqa: E402

SOURCES_OK = {"gold example", "ai-inference"}


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


def test_targeted_forgets_a_sibling_conversion_learning():
    """The exact bug: captured from another conversion, targeted by name -> forget."""
    want = {"insert update indicator"}
    check("sibling-conversion default is forgotten when its field is named",
          _should_forget_default("Customer_Shipping_Address -> Customer Import",
                                 "Insert Update Indicator", want, SOURCES_OK) is True)


def test_targeted_forgets_gold_and_ai_too():
    want = {"insert update indicator"}
    for src in ("gold example", "ai-inference", "gold reference standard"):
        check(f"{src!r} forgotten when named",
              _should_forget_default(src, "Insert Update Indicator", want, SOURCES_OK) is True)


def test_targeted_leaves_other_fields_alone():
    """Naming one field must not sweep up a default for a different field."""
    want = {"insert update indicator"}
    check("a different field is untouched",
          _should_forget_default("Customer_Shipping_Address -> Customer Import",
                                 "Party Type", want, SOURCES_OK) is False)


def test_blanket_reset_stays_conservative():
    """No field named: only gold / AI, never a human-authored sibling learning."""
    check("gold forgotten on blanket reset",
          _should_forget_default("gold example", "Insert Update Indicator", set(), SOURCES_OK) is True)
    check("ai forgotten on blanket reset",
          _should_forget_default("ai-inference", "Whatever", set(), SOURCES_OK) is True)
    check("sibling-conversion learning KEPT on blanket reset",
          _should_forget_default("Customer_Shipping_Address -> Customer Import",
                                 "Insert Update Indicator", set(), SOURCES_OK) is False)


def test_case_and_whitespace_insensitive_on_the_field_name():
    want = {"insert update indicator"}
    check("field match folds case and spaces",
          _should_forget_default("anywhere", "  Insert Update Indicator  ", want, SOURCES_OK) is True)


def test_the_endpoint_uses_the_helper():
    """Guard: the reset path must actually call the helper, not re-inline the old
    source-only filter that shipped the bug."""
    import inspect
    from app.routers import conversions
    src = inspect.getsource(conversions._reset_defaults_impl)
    check("reset uses _should_forget_default", "_should_forget_default(" in src)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nreset-defaults forgets-what-feeds-the-field holds")
