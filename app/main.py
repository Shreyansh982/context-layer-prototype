"""
Application Entry Point

Starts the FastAPI server and registers
all API routes.
"""

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api import router
from app.errors import DependencyUnavailable
from app.observability import ConcurrencyLimitMiddleware, RequestObservabilityMiddleware

# Temporary: make INFO-level diagnostic logs (extraction window/response,
# routing decisions, memory writes) visible in the console while debugging
# the context-layer retrieval/storage pipeline.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Context Layer",
    description="Prototype Context Layer for Agentic AI",
    version="1.0.0"
)

# Register API routes
app.include_router(router)
app.add_middleware(ConcurrencyLimitMiddleware)
app.add_middleware(RequestObservabilityMiddleware)


@app.exception_handler(DependencyUnavailable)
async def dependency_unavailable(_: Request, error: DependencyUnavailable) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(error)})

dashboard_dir = Path(__file__).resolve().parents[1] / "dashboard"
app.mount(
    "/dashboard/static",
    StaticFiles(directory=dashboard_dir),
    name="dashboard-static",
)


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    """Serve the lightweight operator dashboard from the same API origin."""
    return FileResponse(dashboard_dir / "index.html")


@app.on_event("shutdown")
def close_connections() -> None:
    """Release external database resources during a graceful shutdown."""
    from app.storage.neo4j import neo4j

    neo4j.close()
