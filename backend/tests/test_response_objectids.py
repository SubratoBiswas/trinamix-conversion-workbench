"""Response schemas must survive a raw ObjectId — the CW #6 live finding.

``GET /conversions/{id}/rules`` returned 500 in production for any conversion that
HAD a rule. ``TransformationRule.target_field_id`` is a ``PydanticObjectId``,
``TransformationRuleOut.target_field_id`` is a ``str``, Pydantic v2 refuses to coerce,
and the endpoint spread ``model_dump()`` while stringifying only ``id`` and
``conversion_id``. The rule was saved correctly and could not be read back — which
the analyst experiences as "my saved rule disappeared".

Every conversion used in the earlier verification had zero rules, so the endpoint
returned a clean ``[]`` and looked fine. The lesson is the test below: assert the
schema tolerates the type the DATABASE holds, not the type the happy path produces.

Two layers are checked:

  1. Every ``*Out`` response schema in app/schemas accepts ObjectIds in its
     ``str``-typed id fields. This is the general guard — it fails for a NEW schema
     that forgets to inherit ApiOut, not just for the one that broke.
  2. The specific shape ``list_rules`` returns.

Pure: pydantic + bson + stdlib. No DB, no app startup.
"""
import importlib
import os
import pkgutil
import sys
from datetime import datetime

from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_failures = []


def check(name, cond, detail=""):
    """Records AND raises — pytest judges a test by whether it throws."""
    if cond:
        print(f"  PASS  {name}")
        return
    print(f"  FAIL  {name} {detail}")
    _failures.append(name)
    raise AssertionError(f"{name} {detail}".strip())


def _out_schemas():
    """Every ``*Out`` BaseModel subclass declared under app.schemas."""
    import app.schemas as pkg
    found = {}
    for mod in pkgutil.iter_modules(pkg.__path__):
        m = importlib.import_module(f"app.schemas.{mod.name}")
        for name in dir(m):
            obj = getattr(m, name)
            if (isinstance(obj, type) and name.endswith("Out")
                    and hasattr(obj, "model_fields")
                    and obj.__module__ == m.__name__):
                found[f"{mod.name}.{name}"] = obj
    return found


def _unwrap_optional(ann):
    """``Optional[X]`` / ``X | None`` -> ``X``; anything else unchanged.

    Only unions are unwrapped. Unwrapping every generic would turn
    ``dict[str, Any]`` into ``str`` — which is exactly the bug that made the first
    draft of this test report three schemas as broken when the fault was here.
    """
    import typing
    if typing.get_origin(ann) is typing.Union:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return args[0] if len(args) == 1 else ann
    try:                                    # PEP 604 `X | None` on 3.10+
        import types
        if isinstance(ann, types.UnionType):
            args = [a for a in ann.__args__ if a is not type(None)]
            return args[0] if len(args) == 1 else ann
    except Exception:                                           # noqa: BLE001
        pass
    return ann


def _sample(model):
    """A minimal valid payload, with an ObjectId in every id-shaped field."""
    out = {}
    for fname, finfo in model.model_fields.items():
        if fname == "id" or fname.endswith("_id") or fname.endswith("_ids"):
            # THE POINT OF THE TEST: hand it what Mongo actually stores.
            out[fname] = [ObjectId()] if fname.endswith("_ids") else ObjectId()
            continue
        if finfo.is_required():
            out[fname] = _placeholder(_unwrap_optional(finfo.annotation), fname)
    return out


def _placeholder(ann, fname=""):
    import typing
    origin = typing.get_origin(ann)
    if origin in (list, tuple, set) or ann in (list, tuple, set):
        return []
    if origin is dict or ann is dict:
        return {}
    if ann is int:
        return 1
    if ann is float:
        return 1.0
    if ann is bool:
        return True
    if ann is datetime:
        return datetime(2026, 7, 29)
    # EmailStr and friends are str subclasses with extra validation.
    if "email" in fname.lower() or "Email" in str(ann):
        return "someone@example.com"
    return "x"


def test_every_out_schema_tolerates_object_ids_in_id_fields():
    schemas = _out_schemas()
    check("found response schemas", len(schemas) >= 15, f"only {len(schemas)}")
    broken = []
    for name, model in sorted(schemas.items()):
        ids = [f for f in model.model_fields
               if f == "id" or f.endswith("_id") or f.endswith("_ids")]
        if not ids:
            continue
        try:
            model.model_validate(_sample(model))
        except Exception as exc:                                # noqa: BLE001
            broken.append(f"{name}: {type(exc).__name__} on {ids}")
    check("no response schema rejects a stored ObjectId", not broken,
          "\n        " + "\n        ".join(broken))


def test_transformation_rule_out_specifically():
    """The exact failure, pinned. ``target_field_id`` is the field that 500'd."""
    from app.schemas.transformation import TransformationRuleOut
    m = TransformationRuleOut.model_validate({
        "id": ObjectId(), "conversion_id": ObjectId(),
        "target_field_id": ObjectId("6a4675956fa80b71a41408eb"),
        "source_column": "vendor_name", "rule_type": "CONCAT",
        "rule_config": {}, "description": None, "sequence": 1,
        "created_at": datetime(2026, 7, 29),
    })
    check("target_field_id became a string", m.target_field_id == "6a4675956fa80b71a41408eb",
          f"got {m.target_field_id!r}")
    check("it is a str, not an ObjectId", isinstance(m.target_field_id, str))
    check("id coerced too", isinstance(m.id, str))


def test_a_none_reference_stays_none():
    """A rule with no target field is legitimate; coercion must not invent "None"."""
    from app.schemas.transformation import TransformationRuleOut
    m = TransformationRuleOut.model_validate({
        "id": ObjectId(), "conversion_id": ObjectId(), "target_field_id": None,
        "rule_type": "TRIM", "rule_config": {}, "sequence": 0,
        "created_at": datetime(2026, 7, 29),
    })
    check("stays None", m.target_field_id is None, f"got {m.target_field_id!r}")


def test_a_string_id_still_works():
    """Everything already passing strings must keep working unchanged."""
    from app.schemas.transformation import TransformationRuleOut
    m = TransformationRuleOut.model_validate({
        "id": "abc", "conversion_id": "def", "target_field_id": "ghi",
        "rule_type": "TRIM", "rule_config": {}, "sequence": 0,
        "created_at": datetime(2026, 7, 29),
    })
    check("unchanged", (m.id, m.conversion_id, m.target_field_id) == ("abc", "def", "ghi"))


def test_a_list_of_ids_is_coerced_elementwise():
    """``Conversion.dataset_ids`` is a list of ObjectIds."""
    from app.schemas.oid import _coerce
    a, b = ObjectId(), ObjectId()
    check("list", _coerce([a, b]) == [str(a), str(b)])
    check("tuple", _coerce((a,)) == (str(a),))
    check("non-id values untouched", _coerce({"k": 1}) == {"k": 1})


def test_the_coercion_does_not_touch_other_types():
    from app.schemas.oid import _coerce
    for v in ("s", 1, 1.5, True, None, datetime(2026, 7, 29)):
        check(f"{v!r} untouched", _coerce(v) is v or _coerce(v) == v)


def test_list_rules_stringifies_at_the_call_site_too():
    """Belt and braces: the endpoint's own cast is what a reader sees first, so it
    must not silently regress behind the schema-level coercion."""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "app" / "routers" / "mapping.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "list_rules")
    body = ast.unparse(fn)
    check("target_field_id cast present", "str(r.target_field_id)" in body)
    check("excluded from the raw spread",
          "'target_field_id'" in body or '"target_field_id"' in body)


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
