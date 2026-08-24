"""BOM Import structure reshape — RELOCATED to ``app.domain.bom.structure`` (Phase 4).

This module was ~274 lines of pure, DataFrame-only logic — the BOM interface grain
reshape, Item Sequence renumbering, mandatory-field checks and batch stamping. It does
no I/O and imports no framework, so it belongs in the domain. The code moved verbatim;
this file is a thin re-export shim so every caller (``output_service.reshape_for_sheet``)
is unchanged.

Every module-level name — public and private — is re-exported from the domain module by
reference, so ``from app.services.bom_structure_service import reshape_for_sheet`` and any
``bom_structure_service.X`` access keep resolving to the same objects.
"""
from app.domain.bom import structure as _structure

globals().update({_k: _v for _k, _v in vars(_structure).items() if not _k.startswith("__")})
del _structure
