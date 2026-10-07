from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .api import ai, auth, builds, ci, fixes, marketplace, projects, servers, workspaces
from .core.db import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="BUILDBIX", version="0.1.0", description="Build anything, simply.", lifespan=lifespan)
    api = APIRouter(prefix="/api")
    for r in (auth.router, workspaces.router, projects.router, projects.webhooks, builds.router, fixes.router, servers.router, ci.router, ai.router, marketplace.router):
        api.include_router(r)

    @api.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(api)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith(("/docs", "/redoc", "/openapi")):  # Swagger UI loads CDN assets
            resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        return resp

    app.mount("/", StaticFiles(directory=Path(__file__).parent / "web", html=True), name="web")
    return app


app = create_app()
