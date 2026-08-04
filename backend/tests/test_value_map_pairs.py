""""Add Pair does nothing — is this a bug or an intended limitation?"

A bug, and not a two-pair limit: the button could never add a row at all.

The From → To pairs of a VALUE_MAP were derived straight from the config object
and written back with

    next.forEach(([k, v]) => { if (k) out[k] = v; });

`if (k)` drops any pair whose FROM side is empty. Clicking "Add pair" appended
["", ""], the save discarded it immediately, `config` came back unchanged, and
the list re-derived to exactly what it was before. Nothing appeared, no error,
nothing in the console. It read as a limit because the pairs already on screen
stayed and no new one ever joined them.

The `if (k)` is correct for what gets SAVED — a crosswalk entry with no left-hand
side matches nothing and would be dead in the file. It was wrong as the editor's
only memory, because every row is invalid for the moment between creating it and
typing into it.

Two more failures fell out of the same cause and are fixed with it: clearing a
FROM box deleted the whole pair mid-edit, taking the TO value with it; and two
pairs sharing a FROM value silently collapsed into one, because object keys are
unique.

Checked as source, the way the other frontend guarantees here are (test_hook_order,
test_error_boundary) — there is no JS runtime in this suite.
"""
from pathlib import Path

_FE = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"
_MODAL = _FE / "components" / "transforms" / "RuleAuthorModal.tsx"


def _form() -> str:
    src = _MODAL.read_text(encoding="utf-8")
    return src.split("const ValueMapForm")[1].split("\nconst ")[0]


def _code() -> str:
    """The form with its comments stripped.

    Counting occurrences across a body that includes prose counts the PROSE — and
    this file's comment explains the defect by quoting the very expression the
    test is looking for, so a test that reads comments fails on its own
    explanation. That has now cost three tests in one session; strip first."""
    out = []
    for line in _form().splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") \
                or stripped.startswith("/*"):
            continue
        out.append(line)
    return "\n".join(out)


def test_the_editor_keeps_its_own_list_of_rows():
    """The fix. Derived from `config` alone, a row that is not yet valid cannot
    survive one render."""
    body = _form()
    assert "React.useState<[string, string][]>" in body
    assert "setRowsState(next)" in body


def test_the_rows_are_no_longer_re_derived_from_config_on_every_render():
    body = _code()
    derivations = body.count("Object.entries(config)")
    assert derivations == 1, (
        f"expected one read of config, to SEED the list; found {derivations}")
    assert "useState" in body.split("Object.entries(config)")[0][-400:], \
        "the surviving read must be the state initialiser, not a per-render derive"


def test_an_incomplete_pair_is_still_kept_out_of_the_saved_config():
    """Unchanged, and it matters: a crosswalk entry with no left-hand side
    matches nothing and would be silently dead in the generated file."""
    body = _form()
    assert "if (k) out[k] = v;" in body


def test_adding_a_pair_appends_to_the_list_rather_than_to_config():
    src = _MODAL.read_text(encoding="utf-8")
    assert 'setRows([...rows, ["", ""]] as [string, string][])' in src


def test_removing_a_pair_still_works_on_the_list():
    src = _MODAL.read_text(encoding="utf-8")
    assert "setRows(rows.filter((_, j) => j !== i)" in src


def test_an_empty_rule_opens_with_a_row_to_type_into():
    """An empty panel with a button, where the button appeared to do nothing, is
    how this was reported in the first place."""
    assert 'return initial.length ? initial : [["", ""]];' in _form()


def test_the_hook_sits_above_every_return_in_the_component():
    """Rules of Hooks. A conditional hook is what took Output Preview down with
    React #310, and this adds the first hook to this component."""
    body = _code()
    hook = body.index("React.useState")
    early = body.find("\n    return ")
    assert early == -1 or hook < early, "the hook is below a component-level return"
