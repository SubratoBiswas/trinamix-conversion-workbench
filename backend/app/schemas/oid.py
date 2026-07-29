"""``ApiOut`` — response schemas that survive a raw ObjectId.

WHY
---
Beanie stores references as ``PydanticObjectId``; the response schemas declare them
``str``. Pydantic v2 will not coerce one to the other, so any endpoint that hands a
model's ``model_dump()`` to a response model raises ``ResponseValidationError`` — a
500 with the field named in the body — unless the author remembered to ``str()``
that particular field.

The routers do remember, one field at a time:

    {**r.model_dump(), "id": str(r.id), "conversion_id": str(r.conversion_id)}

which is correct for the two ids the author was thinking about and silently wrong for
every other reference on the model. That is how ``GET /conversions/{id}/rules``
returned 500 in production for any conversion that actually had a rule:
``TransformationRule`` carries ``target_field_id`` as well, the spread passed it
through as an ObjectId, and the endpoint the "add custom transformation rule" modal
calls to load existing rules could never return. The rule was saved correctly and was
unreachable — which presents to the analyst as "my saved rule disappeared" (CW #6).
Found on 29-Jul by calling the deployed endpoint; the conversions used in testing had
no rules, so every one of them returned a clean ``[]``.

Rather than add a third ``str()`` and wait for the fourth reference field, coerce once
here. Inheriting this is enough — no call site has to remember anything.
"""
from __future__ import annotations

from typing import Any

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, model_validator


def _coerce(v: Any) -> Any:
    if isinstance(v, ObjectId):          # PydanticObjectId subclasses this
        return str(v)
    if isinstance(v, list):
        return [_coerce(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_coerce(x) for x in v)
    return v


class ApiOut(BaseModel):
    """Base for response schemas: stringifies ObjectIds before validation.

    Only ``dict`` input is rewritten. Attribute-sourced input (``from_attributes``)
    is left alone deliberately — rewriting it would mean copying a Document, and
    every current caller passes a dict.
    """

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _stringify_object_ids(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: _coerce(v) for k, v in data.items()}
        return data
