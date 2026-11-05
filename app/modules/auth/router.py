"""Login, personal profile, password, and logout HTTP routes."""

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
    user = service.current_user(
        settings.data_root,
        request.cookies.get(service.SESSION_COOKIE_NAME),
    )
    if user:
        if user["must_change_password"]:
            return RedirectResponse("/account/password", status_code=303)
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "error": None,
            "message": "密码修改成功，请使用新密码登录"
            if request.query_params.get("password_changed") == "1"
            else None,
            "next_path": _safe_next(next),
            "username": "",
        },
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
                "message": None,
                "next_path": _safe_next(next),
                "username": username.strip(),
            },
            status_code=401,
        )

    token, max_age = service.start_session(settings.data_root, user["id"])
    destination = "/account/password" if user["must_change_password"] else _safe_next(next)
    response = RedirectResponse(destination, status_code=303)
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


def _account_context(
    request: Request,
    *,
    profile_error: str | None = None,
    profile_message: str | None = None,
    password_error: str | None = None,
):
    return {
        "user": request.state.user,
        "profile_error": profile_error,
        "profile_message": profile_message,
        "password_error": password_error,
    }


@router.get("/account/", response_class=HTMLResponse, include_in_schema=False)
def account_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context=_account_context(
            request,
            profile_message="显示名称已更新"
            if request.query_params.get("profile_updated") == "1"
            else None,
        ),
    )


@router.post("/account/profile", response_class=HTMLResponse, include_in_schema=False)
def update_profile(request: Request, display_name: str = Form(...)):
    try:
        service.update_display_name(settings.data_root, request.state.user["id"], display_name)
    except ValueError as exc:
        return templates.TemplateResponse(
            request=request,
            name="account.html",
            context=_account_context(request, profile_error=str(exc)),
            status_code=400,
        )
    return RedirectResponse("/account/?profile_updated=1", status_code=303)


@router.get("/account/password", response_class=HTMLResponse, include_in_schema=False)
def password_page(request: Request):
    if not request.state.user["must_change_password"]:
        return RedirectResponse("/account/#password", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="password_required.html",
        context={"error": None},
    )


@router.post("/account/password", response_class=HTMLResponse, include_in_schema=False)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirmation: str = Form(...),
):
    forced = bool(request.state.user["must_change_password"])
    if new_password != confirmation:
        error = "两次输入的新密码不一致"
    else:
        try:
            service.change_password(
                settings.data_root,
                request.state.user["id"],
                current_password,
                new_password,
            )
        except ValueError as exc:
            error = str(exc)
        else:
            response = RedirectResponse("/login?password_changed=1", status_code=303)
            response.delete_cookie(service.SESSION_COOKIE_NAME, path="/")
            return response

    if forced:
        return templates.TemplateResponse(
            request=request,
            name="password_required.html",
            context={"error": error},
            status_code=400,
        )
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context=_account_context(request, password_error=error),
        status_code=400,
    )


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    service.end_session(
        settings.data_root,
        request.cookies.get(service.SESSION_COOKIE_NAME),
    )
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(service.SESSION_COOKIE_NAME, path="/")
    return response
