"""Multi-source Customer merge — RELOCATED to ``app.domain.customer.merge`` (Phase 4).

This module was ~1,460 lines of pure, DataFrame-only logic (grain classification,
entity enrichment, party linkage, owned-field stamping, DFF/UDCP, contact fan-out,
party numbering, date coercion) living under ``services/``. It does no I/O and imports
no framework, so it belongs in the domain, where the rest of the pure logic now lives.
The code moved verbatim; this file is a thin re-export shim so every caller is unchanged.

The re-export copies every module-level name — public AND private — from the domain
module into this namespace BY REFERENCE, so:

  * ``from app.services.customer_merge import X`` and the namespace access ``_cm.X``
    every caller uses keep resolving to the exact same objects, and
  * ``_OWNED_OVERRIDE`` stays ONE ContextVar object: a ``.set()`` in ``output_service``
    (via ``_cm._OWNED_OVERRIDE``) and the ``.get()`` in the merge logic (in the domain
    module) address the same variable, so the per-sheet owned-field context still works.
"""
from app.domain.customer import merge as _merge

globals().update({_k: _v for _k, _v in vars(_merge).items() if not _k.startswith("__")})
del _merge
