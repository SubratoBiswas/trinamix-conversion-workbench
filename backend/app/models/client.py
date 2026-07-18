"""Client (tenant) model.

A Client is the top-level tenant boundary: an implementation customer (e.g.
NextPower). Projects belong to a Client, and Conversions resolve their Client via
their Project. Client-scoped knowledge — analyst column mappings, transform rules,
gold reference standards, value crosswalks — applies ONLY to that client, so a
future client never inherits NextPower's source-system assumptions.

Knowledge that is Oracle-standard and client-agnostic (the FBDI/HDL templates and
the public-schema mapping catalog) is marked ``is_global`` instead and applies to
every client.
"""
from datetime import datetime
from typing import Optional

from beanie import Document
from pydantic import Field


class Client(Document):
    name: str
    code: Optional[str] = None          # short slug, e.g. "NEXTPOWER"
    description: Optional[str] = None
    # Exactly one client is the default — new projects and the migration attach
    # here when no client is chosen. The bootstrap "NextPower" client is default.
    is_default: bool = False
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "clients"
        indexes = ["name", "is_default"]
