"""Trinamix Conversion Workbench — FastAPI entry point."""
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    from app.seed import run_seed
    await run_seed()
    from app.services.ai_settings import load_persisted_model
    await load_persisted_model()
    # Seed the bundled Oracle FBDI templates (Supplier set + Customer master +
    # Item master) so they're selectable targets without a manual upload.
    try:
        from app.services.template_seed_service import seed_fbdi_templates
        await seed_fbdi_templates()
    except Exception:  # noqa: BLE001 — never block startup on the template seed
        import logging
        logging.getLogger(__name__).exception("fbdi template seed failed")
    # Seed the reusable Mapping Knowledge Base (source→FBDI column mappings for
    # NetSuite / Infor SyteLine / Salesforce) so future files auto-apply them.
    try:
        from app.services.catalog_seed_service import seed_mapping_catalog
        await seed_mapping_catalog()
    except Exception:  # noqa: BLE001 — never block startup on the catalog seed
        import logging
        logging.getLogger(__name__).exception("mapping_catalog seed failed")
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
app.include_router(fusion_router.router)
app.include_router(fusion_router.conv_router)
