"""A page that throws must say what broke, not go white.

React unmounts the whole tree when a render throws and nothing catches it, so
this app's failure mode for any render bug was a blank screen: no message, no
component name, nothing to act on. The console carried `Minified React error
#310` and a stack of one-letter names — not something an analyst can report, and
not much faster for a developer either.

That cost real time on Output Preview: the first explanations considered were a
broken build, a failed deploy and a 404 from the static host, and the screen
looked identical under every one of them. A white page is the least informative
failure a UI can have.

These are source seams, in the same style as the other frontend assertions in
this suite — the frontend has no test runner, and a guarantee nothing checks is
one that quietly goes away.
"""
import re
from pathlib import Path

_FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend" / "src"


def _read(*parts):
    return _FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


def test_the_error_boundary_exists():
    src = _read("components", "ErrorBoundary.tsx")
    assert "class ErrorBoundary" in src


def test_it_is_a_class_because_there_is_no_hook_for_this():
    """`componentDidCatch` and `getDerivedStateFromError` exist only on classes.
    Someone will try to convert this to a function component one day."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "static getDerivedStateFromError" in src
    assert "componentDidCatch" in src


def test_every_route_is_wrapped_by_it():
    """One boundary above the router, so no page can white-screen the app."""
    app = _read("App.tsx")
    assert "<ErrorBoundary" in app
    assert app.index("<ErrorBoundary") < app.index("<Routes>")
    assert app.index("</Routes>") < app.index("</ErrorBoundary>")


def test_navigating_away_from_a_broken_page_recovers():
    """Otherwise one bad route holds the whole app hostage until a reload."""
    app = _read("App.tsx")
    assert "resetKey={pathname}" in app
    src = _read("components", "ErrorBoundary.tsx")
    assert "componentDidUpdate" in src
    assert "prev.resetKey !== this.props.resetKey" in src


def test_it_names_the_component_that_threw():
    """The single most useful thing in the report, and the thing a minified
    stack of one-letter names does not give you."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "componentStack" in src
    assert "function culprit" in src


def test_it_translates_reacts_numbered_production_errors():
    """"#310" is not actionable. "A hook is running conditionally, or sits after
    an early return" is."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "Minified React error #" in src
    for code in ("300", "310", "321"):
        assert f'"{code}"' in src, f"React error {code} has no plain-English form"


def test_310_is_explained_because_it_is_the_one_that_bit():
    src = _read("components", "ErrorBoundary.tsx")
    meaning = re.search(r'"310":\s*(.+?)(?=\n\s*"\d{3}"|\n\};)', src, re.S)
    assert meaning, "no explanation for #310"
    text = meaning.group(1).lower()
    assert "hook" in text and ("early return" in text or "conditionally" in text)


def test_the_details_can_be_copied_in_one_click():
    """So a bug report contains the stack rather than the word "blank"."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "clipboard" in src
    assert "Copy details" in src


def test_the_stack_still_reaches_the_console():
    """The panel deliberately does not dump a wall of stack into the page, so
    the full detail has to remain reachable from devtools."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "console.error" in src
    assert "[ErrorBoundary]" in src


def test_it_tells_the_user_nothing_was_lost():
    """A blank screen reads as "the tool ate my work". It did not."""
    src = _read("components", "ErrorBoundary.tsx")
    assert "rest of the app still works" in src
    assert "has been changed or lost" in src
