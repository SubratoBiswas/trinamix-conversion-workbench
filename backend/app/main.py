"""Trinamix Conversion Workbench — FastAPI entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
        # Mine allowed_values / lookup_type out of the descriptions of templates
        # that were parsed before the LOV parser existed. Cheap (no file IO) and
        # idempotent, so it's safe to run on every boot.
        from app.services.lov_backfill_service import backfill_lov_metadata
        r = await backfill_lov_metadata()
        log.info("startup seed — LOV backfill: %s", r)
    except Exception:  # noqa: BLE001
        log.exception("lov backfill failed")


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
)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "trinamix-conversion-workbench",
        "version": __version__,
        "ai_provider": settings.AI_PROVIDER,
    }


app.include_router(auth_router.router)
app.include_router(datasets_router.router)
app.include_router(fbdi_router.router)
app.include_router(fbdi_seed_router.router)
app.include_router(gold_router.router)
app.include_router(projects_router.router)
app.include_router(conversions_router.router)
app.include_router(cutover_router.router)
app.include_router(mapping_router.router)
app.include_router(quality_router.router)
app.include_router(learned_router.router)
app.include_router(ops_router.output_router)
app.include_router(ops_router.load_router)
app.include_router(ops_router.workflow_router)
app.include_router(ops_router.dep_router)
app.include_router(ops_router.dashboard_router)
# v10
app.include_router(discovery_router.router)
app.include_router(discovery_router.project_router)
app.include_router(audit_router.router)
app.include_router(audit_events_router.router)
app.include_router(coa_router.router)
app.include_router(governance_router.router)
# v10 new
app.include_router(source_systems_router.router)
app.include_router(fusion_modules_router.router)
app.include_router(source_connections_router.router)
app.include_router(cutover_slice6_router.router)
app.include_router(copilot_router.router)
app.include_router(settings_router.router)
app.include_router(clients_router.router)
app.include_router(fusion_router.router)
app.include_router(fusion_router.conv_router)
