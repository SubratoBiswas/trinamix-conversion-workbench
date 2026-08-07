"""The authoring PROMPT must travel with a rule into the shared library.

The analyst types a plain-English (or SQL) instruction to author a transformation
rule; #78 asks for that prompt to be reviewable and re-usable later. For that to work
across projects, the prompt has to be CAPTURED with the client rule — stashed in the
stored rule_config under a reserved `_prompt` key so it rides the same
client+source-scoped propagation as everything else. A rule inherited by a newer
project then still carries the sentence that explains it, instead of reading as a
blank form ("the rules I applied are not reflected in the newer project").

These tests exercise record_learning_from_rule with the database calls mocked, so they
run offline like the rest of the suite.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import learning_service as LS            # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}"); return
    raise AssertionError(f"{name} {detail}".strip())


class _Rule:
    def __init__(self, rule_type="CONDITIONAL", config=None, source_column=None,
                 prompt=None, description=None, target_field_id="tfid"):
        self.rule_type = rule_type
        self.rule_config = config or {"if_column": "Worker_Type", "equals": "Employee",
                                      "then": "E", "else": ""}
        self.source_column = source_column
        self.prompt = prompt
        self.description = description
        self.target_field_id = target_field_id


class _Conv:
    id = "convid"
    name = "Oracle_Fusion_Demographic_04_08_2026_4"
    project_id = "projid"
    template_id = "tplid"


class _Field:
    field_name = "AssignmentNumber"


def _run_capture(rule, monkey_upsert):
    """Drive record_learning_from_rule with every DB touch stubbed; return the
    kwargs the store was asked to write."""
    captured = {}

    async def fake_upsert(**kwargs):
        # The first call is the client rule; keep it. (A master-key field would make a
        # second reference_standard call — AssignmentNumber is not one, so there is
        # only one call here.)
        if not captured:
            captured.update(kwargs)
        return object()

    async def fake_business_object(_conv):
        return "Employee HDL"

    async def fake_field_get(_id):
        return _Field()

    async def fake_source_erp(_conv):
        return "workday"

    async def fake_client_id(_conv):
        return None

    # Patch the module-level names record_learning_from_rule reaches.
    orig = {
        "_upsert": LS._upsert,
        "_business_object_for": LS._business_object_for,
        "source_erp_for_conversion": LS.source_erp_for_conversion,
    }
    LS._upsert = fake_upsert
    LS._business_object_for = fake_business_object
    LS.source_erp_for_conversion = fake_source_erp

    # FBDIField.get and client_id_for_conversion are imported inside the function.
    import app.models.fbdi as fbdi
    import app.services.client_service as cs
    orig_field_get = fbdi.FBDIField.get
    orig_client = cs.client_id_for_conversion
    fbdi.FBDIField.get = staticmethod(fake_field_get)
    cs.client_id_for_conversion = fake_client_id
    try:
        asyncio.get_event_loop().run_until_complete(
            LS.record_learning_from_rule(rule, _Conv(), captured_by="a@b.c"))
    finally:
        LS._upsert = orig["_upsert"]
        LS._business_object_for = orig["_business_object_for"]
        LS.source_erp_for_conversion = orig["source_erp_for_conversion"]
        fbdi.FBDIField.get = orig_field_get
        cs.client_id_for_conversion = orig_client
    return captured


def test_prompt_is_stashed_in_the_stored_config():
    cap = _run_capture(_Rule(prompt="if Worker Type is Employee then 'E'+Employee_ID"),
                       None)
    check("stored as a client rule (target_object None)", cap.get("target_object") is None)
    check("kind is rule", cap.get("kind") == "rule")
    cfg = cap.get("rule_config") or {}
    check("_prompt captured into config",
          cfg.get("_prompt") == "if Worker Type is Employee then 'E'+Employee_ID", cfg)
    # The functional config is untouched beside it.
    check("original config keys preserved",
          cfg.get("if_column") == "Worker_Type" and cfg.get("then") == "E", cfg)
    check("scoped to the source system", cap.get("source_erp") == "workday")


def test_description_is_used_when_no_prompt():
    cap = _run_capture(_Rule(prompt=None, description="derive the assignment number"), None)
    cfg = cap.get("rule_config") or {}
    check("description falls back into _prompt",
          cfg.get("_prompt") == "derive the assignment number", cfg)


def test_no_prompt_leaves_config_clean():
    cap = _run_capture(_Rule(prompt=None, description=None), None)
    cfg = cap.get("rule_config") or {}
    check("no _prompt key when nothing was typed", "_prompt" not in cfg, cfg)
    check("config still carries the real rule", cfg.get("if_column") == "Worker_Type", cfg)


def test_capture_does_not_mutate_the_rules_own_config():
    r = _Rule(prompt="hello")
    _run_capture(r, None)
    check("rule.rule_config was copied, not mutated", "_prompt" not in r.rule_config,
          r.rule_config)


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(fn.__name__); fn()
    print("\nPrompt capture: the authoring prompt rides the client rule's config.")
