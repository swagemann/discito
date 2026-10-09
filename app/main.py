"""Discito — Verse Memorizer.

FastAPI + Jinja2 app for one household: children practice reciting passages
(graded by a faster-whisper sidecar) and spelling lists; a parent manages
assignments and reviews progress. All state lives in one SQLite file.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.routes import admin, api, health, kids
from app.web import BASE_DIR, LoginRequired, UnlockRequired

CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; media-src 'self' blob:; font-src 'self'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.startup")
    if settings.env == "prod" and settings.secret_key.startswith("change-me"):
        log.warning("config.insecure_secret_key")
    if settings.parent_password == "change-me":
        log.warning("config.default_parent_password")
    log.info("app.starting", env=settings.env)
    yield
    log.info("app.stopping")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Discito", lifespan=lifespan, docs_url=None, redoc_url=None)

    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="discito",
        max_age=180 * 24 * 3600,
        same_site="lax",
        https_only=settings.cookie_secure,
    )

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "microphone=(self), camera=()")
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    @app.exception_handler(LoginRequired)
    async def to_login(request: Request, _exc: LoginRequired) -> Response:
        return RedirectResponse(f"/parent/login?next={request.url.path}", status_code=303)

    @app.exception_handler(UnlockRequired)
    async def to_unlock(request: Request, _exc: UnlockRequired) -> Response:
        return RedirectResponse("/unlock", status_code=303)

    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(health.router)
    app.include_router(kids.router)
    app.include_router(api.router)
    app.include_router(admin.router)
    app.include_router(admin.guarded)
    return app


app = create_app()
