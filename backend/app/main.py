"""Trinamix Conversion Workbench — FastAPI entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.routers import auth as auth_router
from app.routers import conversions as conversions_router
from app.routers import cutover as cutover_router
from app.routers import datasets as datasets_router
from app.routers import fbdi as fbdi_router
from app.routers import learned as learned_router
from app.routers import mapping as mapping_router
from app.routers import operations as ops_router
from app.routers import projects as projects_router
from app.routers import quality as quality_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-seed on startup so the demo is always ready
    from app.seed import run_seed
    run_seed()
    yield


app = FastAPI(
    title="Trinamix Conversion Workbench",
    description="AI-powered Oracle Fusion data conversion and migration workbench.",
    version=__version__,
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
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
