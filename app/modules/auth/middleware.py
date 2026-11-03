"""Require an authenticated browser session for platform routes."""

from time import perf_counter
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.modules.auth import service
from app.modules.system.service import write_request_log

PUBLIC_PATHS = frozenset({"/login", "/healthz", "/static"})
PUBLIC_PREFIXES = ("/static/",)


def _current_user(request: Request) -> dict | None:
    return service.current_user(
        settings.data_root,
        request.cookies.get(service.SESSION_COOKIE_NAME),
    )


def _is_api_request(request: Request) -> bool:
    return (
        "/api/" in request.url.path
        or request.url.path.startswith("/api")
        or request.method not in {"GET", "HEAD"}
        or "application/json" in request.headers.get("accept", "")
    )


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            return await call_next(request)

        user = _current_user(request)
        if user is None:
            if _is_api_request(request):
                return JSONResponse({"detail": "请先登录"}, status_code=401)
            target = path
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

        request.state.user = user
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            write_request_log(
                settings.data_root,
                username=str(user.get("username") or "unknown"),
                method=request.method,
                path=path,
                status_code=500,
                duration_ms=(perf_counter() - started) * 1000,
            )
            raise
        write_request_log(
            settings.data_root,
            username=str(user.get("username") or "unknown"),
            method=request.method,
            path=path,
            status_code=response.status_code,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return response
