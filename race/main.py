import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from race.api import router
from race.config import Settings
from race.db import Database
from race.errors import AppError
from race.service import WorkspaceService

BASE = Path(__file__).parent
log = logging.getLogger("race")


def create_app(settings=None):
    settings = settings or Settings()
    db = Database(settings)

    app = FastAPI(title="RACE Workspace API", version="0.1.0")
    app.state.settings = settings
    app.state.service = WorkspaceService(db)
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
    templates = Jinja2Templates(directory=BASE / "templates")
    app.include_router(router)

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        # Never log exception text: connectors may include SQL parameters or credentials.
        log.error(
            json.dumps(
                {
                    "event": "unhandled_error",
                    "type": type(exc).__name__,
                    "request_id": getattr(request.state, "request_id", None),
                }
            )
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "The service is temporarily unavailable. Check configuration and retry."},
        )

    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        started = time.monotonic()
        request.state.request_id = str(uuid4())
        # JSON endpoints have no form-compatible mutations. Reject cross-origin browser writes.
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse(
                    status_code=403, content={"detail": "Cross-site requests are not allowed"}
                )
            if (
                request.headers.get("content-length", "0").isdigit()
                and int(request.headers.get("content-length", "0")) > 262144
            ):
                return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
            if request.method != "DELETE" and not request.headers.get("content-type", "").startswith(
                "application/json"
            ):
                return JSONResponse(status_code=415, content={"detail": "Use application/json"})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src blob:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
        )
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        log.info(
            json.dumps(
                {
                    "event": "request",
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "request_id": request.state.request_id,
                }
            )
        )
        return response

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"mode": settings.mode})

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with db.session() as session:
                result = session.execute(f"SELECT version FROM {db.table('schema_versions')} WHERE version=1")
                if not result:
                    raise RuntimeError("Migrations required")
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "not ready", "detail": "Initialize metadata and check warehouse access"},
            )
        return {
            "status": "ready",
            "mode": settings.mode,
            "metadata_store": "unity-catalog-delta",
            "metadata_schema": f"{settings.metadata_catalog}.{settings.metadata_schema}",
        }

    return app
