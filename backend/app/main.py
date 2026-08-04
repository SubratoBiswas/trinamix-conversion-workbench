"""Trinamix Conversion Workbench — FastAPI entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import datetime as _dt

from app import __version__
from app.config import settings
from app.database import init_db
from app.routers import auth as auth_router
from app.routers import conversions as conversions_router
from app.routers import cutover as cutover_router
from app.routers import datasets as datasets_router
from app.routers import fbdi as fbdi_router
from app.routers import fbdi_seed as fbdi_seed_router
from app.routers import gold as gold_router
from app.routers import learned as learned_router
from app.routers import mapping_proposals as mapping_proposals_router
from app.routers import manual_map as manual_map_router
from app.routers import mapping as mapping_router
from app.routers import operations as ops_router
from app.routers import projects as projects_router
from app.routers import quality as quality_router
from app.routers import discovery as discovery_router
from app.routers import audit as audit_router
from app.routers import audit_events as audit_events_router
from app.routers import coa as coa_router
from app.routers import governance as governance_router
# v10 new routers
from app.routers import source_systems as source_systems_router
from app.routers import fusion_modules as fusion_modules_router
from app.routers import source_connections as source_connections_router
from app.routers import cutover_slice6 as cutover_slice6_router
from app.routers import copilot as copilot_router
from app.routers import fusion as fusion_router
from app.routers import settings as settings_router
from app.routers import clients as clients_router
from app.routers import dq_rules as dq_rules_router


async def _run_seeds_background() -> None:
    """Bundled FBDI templates + the source→FBDI mapping catalog.

    Deliberately NOT awaited in `lifespan`: parsing the template workbooks and
    inserting their field rows takes long enough to delay readiness on a small
    instance (which made the first post-deploy requests time out). Both seeds are
    idempotent, so running them concurrently with live traffic is safe.
    """
    log = logging.getLogger(__name__)
    try:
        # Tenant bootstrap: ensure the NextPower (default) client exists and tag all
        # pre-multi-tenant data (templates+catalog global; everything else NextPower).
        # Idempotent — a no-op once tagged.
        from app.services.client_service import run_client_scope_migration
        r = await run_client_scope_migration()
        log.info("startup seed — client scope migration: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("client scope migration failed")
    try:
        from app.services.template_seed_service import seed_fbdi_templates
        r = await seed_fbdi_templates()
        log.info("startup seed — fbdi templates: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("fbdi template seed failed")
    try:
        # Auto-repair: guarantee the real 19-sheet Customer Import exists and that
        # Customer conversions point at it (not a flat synthetic namesake). Cheap +
        # idempotent — no inline AI mapping, so it never blocks or times out.
        from app.services.template_seed_service import ensure_customer_multisheet
        r = await ensure_customer_multisheet()
        log.info("startup seed — customer template repair: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("customer template repair failed")
    try:
        # Same repair for Item: guarantee the real 17-sheet EGP_SYSTEM_ITEMS
        # template exists and Item conversions point at it (not a flat synthetic
        # namesake seeded from the generic itemmasterimport schema).
        from app.services.template_seed_service import ensure_item_multisheet
        r = await ensure_item_multisheet()
        log.info("startup seed — item template repair: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("item template repair failed")
    try:
        from app.services.catalog_seed_service import seed_mapping_catalog
        r = await seed_mapping_catalog()
        log.info("startup seed — mapping catalog: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("mapping_catalog seed failed")
    try:
        # Analyst-confirmed NextPower Item standard-field mappings (Arena/SyteLine/
        # NetSuite/Anaplan → Oracle item columns) so item conversions auto-map.
        from app.services.catalog_seed_service import seed_item_field_mappings
        r = await seed_item_field_mappings()
        log.info("startup seed — item field mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("item field mapping seed failed")
    try:
        # Analyst-confirmed NextPower Supplier mappings (NetSuite SS Vendors + Arena
        # eBOS → the 6 supplier interface objects), incl. Business Relationship
        # value-map — so supplier conversions auto-map from either source.
        from app.services.catalog_seed_service import seed_supplier_field_mappings
        r = await seed_supplier_field_mappings()
        log.info("startup seed — supplier field mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier field mapping seed failed")
    try:
        # Analyst-confirmed supplier transforms (Raman feedback): Delivery Method/
        # Channel derivation, Phone/Fax split, Use Withholding Tax <- Default WT Code.
        from app.services.catalog_seed_service import seed_supplier_transform_mappings
        r = await seed_supplier_transform_mappings()
        log.info("startup seed — supplier transform rules: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier transform seed failed")
    try:
        # Analyst-approved NextPower supplier constant defaults (e.g. Supplier Site
        # Invoice Match Option = Receipt) as example_default learnings.
        from app.services.catalog_seed_service import seed_supplier_default_values
        r = await seed_supplier_default_values()
        log.info("startup seed — supplier default values: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier default values seed failed")
    try:
        # NextPower Supplier Conversion Strategy v1.0 section 7 — the SIGNED
        # functional spec. Seeded last of the supplier seeds so its values win on
        # a clash, and ahead of the deterministic control constants at generate.
        from app.services.catalog_seed_service import seed_supplier_strategy_defaults
        r = await seed_supplier_strategy_defaults()
        log.info("startup seed — supplier conversion strategy: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier conversion strategy seed failed")
    try:
        # Analyst corrections of 30-Jul. Seeded LAST of the supplier seeds so the most
        # recent instruction wins a clash. A mapping a person has since edited and
        # approved in the UI still outranks all of it.
        from app.services.catalog_seed_service import seed_supplier_corrections_30jul
        r = await seed_supplier_corrections_30jul()
        log.info("startup seed — supplier corrections 30-Jul: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier corrections seed failed")
    try:
        # The GREEN rows of NXT Supplier Mapping_30Jul26 — the workbook's own legend
        # for "Mapped". Seeded after the strategy so the signed spec still wins a
        # clash, and keyed by source system so NetSuite's and SyteLine's mappings for
        # the same field stay two rows rather than one overwriting the other.
        from app.services.catalog_seed_service import seed_supplier_source_mapping
        r = await seed_supplier_source_mapping()
        log.info("startup seed — supplier source mapping (30Jul green): %s", r)
    except Exception:  # noqa: BLE001
        log.exception("supplier source mapping seed failed")
    try:
        # Per-SHEET Customer scope (CW_Issues 2 rows 13-15, 26). The mechanism for
        # this shipped a while ago and not one seeded learning used it, so
        # "map id in all sheets EXCEPT HZ_IMP_CLASSIFICS_T" was applied to all
        # sheets including that one. Seeded AFTER the customer field mappings so the
        # scope lands on the rows they create.
        from app.services.catalog_seed_service import seed_customer_sheet_scope
        r = await seed_customer_sheet_scope()
        log.info("startup seed — customer per-sheet scope: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("customer sheet scope seed failed")
    try:
        # GREEN rows of the NXT HCM Field Mapping workbook (Workday -> Employee HDL).
        # Seeded after the base HDL mappings so the analyst's sheet wins a clash.
        from app.services.catalog_seed_service import seed_hcm_source_mapping
        r = await seed_hcm_source_mapping()
        log.info("startup seed — HCM green mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("HCM source mapping seed failed")
    try:
        # Analyst-confirmed NextPower Customer mappings (NetSuite → the 19-sheet
        # Fusion Customer Import): account/party key references + name/tax/email/
        # phone/credit, propagated by field name across the interface sheets.
        from app.services.catalog_seed_service import seed_customer_field_mappings
        r = await seed_customer_field_mappings()
        log.info("startup seed — customer field mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("customer field mapping seed failed")
    try:
        # HCM Employee HDL loader: seed the .dat template (components + attributes)
        # so it's a first-class conversion target, then its Workday source mappings.
        from app.services.hdl_seed_service import ensure_employee_hdl
        r = await ensure_employee_hdl()
        log.info("startup seed — employee HDL template: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("employee HDL template seed failed")
    try:
        from app.services.catalog_seed_service import seed_employee_hdl_field_mappings
        r = await seed_employee_hdl_field_mappings()
        log.info("startup seed — employee HDL field mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("employee HDL field mapping seed failed")
    try:
        # BOM / Item Structure: force-seed the real 4-sheet bundled workbook
        # (EGP_STRUCTURES/COMPONENTS/SUB_COMPS/REF_DESGS_INTERFACE) so a from-scratch
        # DB has it and BOM conversions don't get stuck on the thin BomImport.
        from app.services.template_seed_service import ensure_bom_multisheet
        r = await ensure_bom_multisheet()
        log.info("startup seed — BOM Item Structure template: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("BOM template seed failed")
    try:
        # Analyst-confirmed NextPower BOM / Item Structure mappings (Arena Tracker +
        # eBOS → EGP_STRUCTURES/COMPONENTS_INTERFACE): source→target columns for both
        # vocabularies plus the fixed constant defaults (Transaction Type=SYNC,
        # Structure Name=Primary, Organization Code=NXT_ITEM_ORG).
        from app.services.catalog_seed_service import seed_bom_field_mappings
        r = await seed_bom_field_mappings()
        log.info("startup seed — BOM field mappings: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("BOM field mapping seed failed")
    try:
        # NextPower Item DO-NOT-MAP list: NetSuite custom source columns the analyst
        # excluded (AI was over-mapping them). Seeded as ignore_source learnings so
        # the mapper leaves those source columns unmapped for future Item imports.
        from app.services.catalog_seed_service import seed_item_donotmap_columns
        r = await seed_item_donotmap_columns()
        log.info("startup seed — item do-not-map columns: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("item do-not-map seed failed")

    try:
        # The two Customer documents of 03-Aug: the field-mapping workbook's green
        # rows plus customer_mapping.txt's transformation rules and defaults.
        # Seeded AFTER seed_customer_field_mappings and seed_customer_sheet_scope,
        # and dated 2026-08-03, so it wins every earlier Customer statement it
        # contradicts and loses to anything said since. There is no fan-out to
        # existing projects: every conversion resolves against the store at
        # generate time, which is what makes "apply to all projects, past and
        # future" a property of the store rather than a job.
        from app.services.catalog_seed_service import seed_customer_mapping_03aug
        r = await seed_customer_mapping_03aug()
        log.info("startup seed — customer mapping 03-Aug: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("customer 03-Aug mapping seed failed")

    try:
        # Mine allowed_values / lookup_type out of the descriptions of templates
        # that were parsed before the LOV parser existed. Cheap (no file IO) and
        # idempotent, so it's safe to run on every boot.
        from app.services.lov_backfill_service import backfill_lov_metadata
        r = await backfill_lov_metadata()
        log.info("startup seed — LOV backfill: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("lov backfill failed")

    try:
        # THE ONE DATED STORE — carry every decision made before it existed into
        # it, so existing projects keep their history. Runs LAST, after the
        # seeders, so a decision an analyst made in the grid is compared against a
        # library that is already up to date. Idempotent, and it re-stamps nothing:
        # a run with nothing to do writes nothing.
        from app.services.mapping_store_backfill import backfill
        r = await backfill()
        log.info("startup seed — one dated store backfill: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("one dated store backfill failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from app.seed import run_seed
    await run_seed()
    from app.services.ai_settings import load_persisted_model
    await load_persisted_model()
    # Seeds run OFF the startup path. Parsing the bundled FBDI templates (the
    # Item workbook alone is 17 sheets / ~1.4k columns) and writing thousands of
    # field rows is far too slow to sit in `lifespan` — it delays readiness and
    # makes the first requests after a deploy time out on a small instance.
    # Fire-and-forget instead: the app serves immediately and the seeds populate
    # a moment later. Both are idempotent, so a restart mid-seed is safe.
    asyncio.create_task(_run_seeds_background())
    yield


app = FastAPI(
    title="Trinamix Conversion Workbench",
    description="AI-powered Oracle Fusion data conversion and migration workbench.",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Frontend runs cross-origin (separate backend host), so the browser hides
    # response headers from JS unless they're explicitly exposed. Downloads need
    # Content-Disposition so the client saves the file under its REAL name/extension
    # (a supplier FBDI output is a .zip, not .csv) instead of a forced .csv.
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request, exc):
    """Return unhandled server errors as a clean 500 WITH CORS headers. Starlette's
    default 500 is generated above the CORS middleware, so it carries no
    Access-Control-Allow-Origin header and the browser reports it as an opaque CORS
    / ERR_FAILED error — which hid a simple bug as a 'CORS' problem. This makes every
    server error a diagnosable JSON response the frontend can display."""
    import logging as _lg
    from fastapi.responses import JSONResponse
    _lg.getLogger(__name__).exception("unhandled error on %s", getattr(request, "url", "?"))
    return JSONResponse(
        status_code=500,
        content={"detail": f"Server error: {type(exc).__name__}: {str(exc)[:200]}"},
        headers={"Access-Control-Allow-Origin": "*"},
    )


_STARTED_AT = _dt.datetime.utcnow()


@app.get("/api/health")
def health() -> dict:
    """Liveness, and WHICH BUILD IS ANSWERING.

    ``version`` is a constant in app/__init__.py and has been "0.1.0" throughout, so
    it could never answer the question that actually gets asked - "is the fix I just
    pushed the code that is running?" Several rounds of "it does not work" / "that is
    not deployed yet" were spent on both sides guessing at that, with a screenshot as
    the only evidence and no way to tell a missing fix from an un-deployed one.

    Render exposes the deployed commit as RENDER_GIT_COMMIT, so the running build can
    simply say so. Unset (local, or another host) reports "unknown" rather than
    pretending.
    """
    import os
    _sha = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "").strip()
    return {
        "status": "ok",
        "service": "trinamix-conversion-workbench",
        "version": __version__,
        "ai_provider": settings.AI_PROVIDER,
        "commit": (_sha[:12] or "unknown"),
        "branch": (os.getenv("RENDER_GIT_BRANCH") or "").strip() or None,
        "started_at": _STARTED_AT.isoformat() + "Z",
    }


# ===========================================================================
# ROUTER REGISTRATION IS THE ACCESS CONTROL.
#
# Every router is mounted through access_control.mount with an explicit section.
# The section is keyword-only and has no default, so a router added without one
# is a TypeError at import rather than a silently public endpoint — which is the
# failure this whole mechanism exists to make impossible. See
# services/access_control.py for what each section means.
#
#   OPEN       Home, Conversion Workbench, Load Management — the three sections a
#              Normal user keeps. Every method.
#   READ_ONLY  Libraries the open screens READ from. Any signed-in user may GET;
#              changing them needs an administrator.
#   ADMIN      Everything else, every method.
#   PUBLIC     No blanket guard; the router authenticates its own routes.
# ===========================================================================
from app.services.access_control import mount as _mount, PUBLIC, OPEN, READ_ONLY, ADMIN  # noqa: E402

# Sign-in itself cannot require being signed in.
_mount(app, auth_router.router, section=PUBLIC, name="auth")

# --- Conversion Workbench ---------------------------------------------------
_mount(app, clients_router.router, section=OPEN, name="clients")
_mount(app, projects_router.router, section=OPEN, name="projects")
_mount(app, conversions_router.router, section=OPEN, name="conversions")
_mount(app, mapping_router.router, section=OPEN, name="mapping")
_mount(app, mapping_proposals_router.router, section=OPEN, name="mapping_proposals")
_mount(app, manual_map_router.router, section=OPEN, name="manual_map")
_mount(app, quality_router.router, section=OPEN, name="quality")
_mount(app, ops_router.output_router, section=OPEN, name="operations.output")
_mount(app, ops_router.workflow_router, section=OPEN, name="operations.workflow")

# --- Load Management --------------------------------------------------------
_mount(app, ops_router.load_router, section=OPEN, name="operations.load")
_mount(app, ops_router.dep_router, section=OPEN, name="operations.dependencies")
_mount(app, cutover_router.router, section=OPEN, name="cutover")
_mount(app, cutover_slice6_router.router, section=OPEN, name="cutover_slice6")
_mount(app, fusion_router.router, section=OPEN, name="fusion")
_mount(app, fusion_router.conv_router, section=OPEN, name="fusion.conversions")

# --- Home -------------------------------------------------------------------
_mount(app, ops_router.dashboard_router, section=OPEN, name="operations.dashboard")

# The Copilot button floats on every page, including the three open sections.
# Locking it to administrators would leave a Normal user a control that errors
# everywhere it appears; it answers questions about a conversion they can
# already see.
_mount(app, copilot_router.router, section=OPEN, name="copilot")

# --- Libraries the open screens read from -----------------------------------
# Mapping Review, Recommendations, Project Overview and Conversion Detail all
# read these. A Normal user may look; uploading a dataset, editing a gold
# standard, seeding template fields or approving a learning is administrative.
_mount(app, datasets_router.router, section=READ_ONLY, name="datasets")
_mount(app, fbdi_router.router, section=READ_ONLY, name="fbdi")
_mount(app, fbdi_seed_router.router, section=READ_ONLY, name="fbdi_seed")
_mount(app, gold_router.router, section=READ_ONLY, name="gold")
_mount(app, learned_router.router, section=READ_ONLY, name="learned")
_mount(app, source_systems_router.router, section=READ_ONLY, name="source_systems")
_mount(app, fusion_modules_router.router, section=READ_ONLY, name="fusion_modules")
_mount(app, audit_events_router.router, section=READ_ONLY, name="audit_events")
# Migration Monitor reads issues, risks, sign-offs and rehearsals. Raising a risk
# or signing off a cutover is an administrator's decision.
_mount(app, governance_router.router, section=READ_ONLY, name="governance")

# --- Administrators only ----------------------------------------------------
_mount(app, audit_router.router, section=ADMIN, name="audit")
_mount(app, coa_router.router, section=ADMIN, name="coa")
_mount(app, dq_rules_router.router, section=ADMIN, name="dq_rules")
_mount(app, settings_router.router, section=ADMIN, name="settings")
# Source connections hold credentials, and discovery drives them.
_mount(app, discovery_router.router, section=ADMIN, name="discovery")
_mount(app, discovery_router.project_router, section=ADMIN, name="discovery.project")
_mount(app, source_connections_router.router, section=ADMIN, name="source_connections")
