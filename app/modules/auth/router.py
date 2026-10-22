"""Login and logout HTTP routes."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import settings
from app.modules.auth import service
from app.web import templates

router = APIRouter()


def _safe_next(value: str) -> str:
    value = value.strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/dashboard/"
    return value


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, next: str = "/dashboard/"):
    if service.current_user(
        settings.data_root,
        request.cookies.get(service.SESSION_COOKIE_NAME),
    ):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None, "next_path": _safe_next(next), "username": ""},
    )


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard/"),
):
    user = service.authenticate(settings.data_root, username, password)
    if user is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "error": "用户名或密码错误",
                "next_path": _safe_next(next),
                "username": username.strip(),
            },
            status_code=401,
        )

    token, max_age = service.start_session(settings.data_root, user["id"])
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(
        service.SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    service.end_session(
        settings.data_root,
        request.cookies.get(service.SESSION_COOKIE_NAME),
    )
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(service.SESSION_COOKIE_NAME, path="/")
    return response
