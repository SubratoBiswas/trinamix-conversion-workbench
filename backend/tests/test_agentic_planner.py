"""Unit tests for the agentic plan-step planner (pure)."""
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from app.services.agentic_planner import plan_steps_for  # noqa: E402


def _actions(steps):
    return [s["action"] for s in steps]


def test_no_source_is_single_blocker():
    steps = plan_steps_for(dict(has_source=False, unmapped_required=5, has_mappings=False))
    assert len(steps) == 1 and steps[0]["blocker"] is True
    assert "source" in steps[0]["action"].lower()


def test_gaps_plan_maps_then_generates_then_validates():
    steps = plan_steps_for(dict(has_source=True, unmapped_required=6, has_mappings=True,
                                dq_generated=False, dq_hard_errors=0))
    a = _actions(steps)
    assert a[0].startswith("Auto-map 6")
    assert "Generate merged output" in a and "Run pre-load validation" in a
    assert not any(s["blocker"] for s in steps)


def test_unmapped_all_when_no_mappings():
    steps = plan_steps_for(dict(has_source=True, unmapped_required=0, has_mappings=False))
    assert _actions(steps)[0] == "Auto-map all fields"


def test_dq_hard_errors_add_blocker_step():
    steps = plan_steps_for(dict(has_source=True, unmapped_required=0, has_mappings=True,
                                output_generated=True, dq_generated=True, dq_hard_errors=3))
    assert any(s["blocker"] and "3" in s["action"] for s in steps)


def test_ready_plan_has_no_blockers():
    steps = plan_steps_for(dict(has_source=True, unmapped_required=0, has_mappings=True,
                                output_generated=True, dq_generated=True, dq_hard_errors=0))
    assert not any(s["blocker"] for s in steps)
    assert "Generate merged output" in _actions(steps)


def test_every_step_names_a_layer():
    steps = plan_steps_for(dict(has_source=True, unmapped_required=2, has_mappings=True))
    assert all(s.get("layer") for s in steps)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = 0
    for t in tests:
        t(); print("PASS ", t.__name__); p += 1
    print(f"\n{p}/{len(tests)} passed")
