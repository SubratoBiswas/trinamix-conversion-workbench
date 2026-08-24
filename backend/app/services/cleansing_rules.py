"""Value-level cleansing rules — RELOCATED to ``app.domain.cleansing`` (Phase 4).

This module was ~393 lines of pure, DataFrame-only cleansing logic (whitespace/punct
normalisation, special-character folding, case/title-casing, legal-suffix canonicalisation,
and the per-column family profile). It does no I/O and imports no framework, so it belongs
in the domain. The code moved verbatim; this file is a thin re-export shim so every caller
is unchanged — ``routers/operations`` (namespace ``cr.X``) and ``generate_dq``
(``cleanse_frame`` / ``_norm_protect``).

Every module-level name — public and private — is re-exported from the domain module by
reference, so those imports and the ``cr.X`` access keep resolving to the same objects.
"""
from app.domain import cleansing as _cleansing

globals().update({_k: _v for _k, _v in vars(_cleansing).items() if not _k.startswith("__")})
del _cleansing
