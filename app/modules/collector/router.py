"""Bearer-token HTTP API used by distributed robot collection clients."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.robot_tokens import RobotTokenRepository
from app.modules.collector import service
from app.modules.collector.schemas import RebindDuplicate, UploadPreflight
from app.modules.ingestion.service import receive_dataset_upload

router = APIRouter()


def _error(status_code: int, code: str, message: str, **details) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "code": code, "message": message, **details},
    )


def _authenticate(authorization: str | None) -> tuple[dict | None, JSONResponse | None]:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        return None, _error(401, "token_required", "请使用 Bearer Token 认证。")
    robot = RobotTokenRepository(settings.data_root).authenticate(token)
    if robot is None:
        return None, _error(401, "invalid_token", "机器人 Token 无效。")
    if robot["status"] == "inactive":
        return None, _error(403, "robot_inactive", "机器人已停用，禁止上传数据。")
    return robot, None


def _rule_error(exc: service.CollectorRuleError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


@router.get("/profile")
def collector_profile(
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    robot, error = _authenticate(authorization)
    if error:
        return error
    return JSONResponse(
        content={
            "ok": True,
            "robot": {
                "id": robot["id"],
                "name": robot["name"],
                "robot_type": robot["robot_type"],
                "status": robot["status"],
            },
        }
    )


@router.post("/uploads/preflight")
def upload_preflight(
    payload: UploadPreflight,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    robot, error = _authenticate(authorization)
    if error:
        return error
    try:
        result = service.preflight(
            settings.data_root,
            robot,
            archive_sha256=payload.archive_sha256,
            archive_size=payload.archive_size,
            archive_robot_type=payload.archive_robot_type,
            robot_type_confirmation=payload.robot_type_confirmation,
        )
    except service.CollectorRuleError as exc:
        return _rule_error(exc)
    return JSONResponse(content=result)

@router.post("/uploads")
async def upload_dataset(
    archive: Annotated[UploadFile, File()],
    archive_sha256: Annotated[str, Form()],
    archive_size: Annotated[int, Form()],
    archive_robot_type: Annotated[str, Form()] = "",
    robot_type_confirmation: Annotated[str, Form()] = "",
    dataset_name: Annotated[str, Form()] = "",
    description: Annotated[str, Form()] = "",
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    robot, error = _authenticate(authorization)
    if error:
        await archive.close()
        return error
    try:
        checked = service.preflight(
            settings.data_root,
            robot,
            archive_sha256=archive_sha256,
            archive_size=archive_size,
            archive_robot_type=archive_robot_type or None,
            robot_type_confirmation=robot_type_confirmation or None,
        )
        if checked["action"] != "upload":
            await archive.close()
            return JSONResponse(content=checked)
        service.begin_upload(
            settings.data_root,
            robot,
            archive_sha256=archive_sha256,
            filename=archive.filename or "dataset.zip",
            archive_size=archive_size,
        )
    except service.CollectorRuleError as exc:
        await archive.close()
        return _rule_error(exc)

    filename = Path(archive.filename or "dataset.zip").name
    default_name = Path(filename).stem.strip() or "机器人上传数据集"
    if len(default_name) < 2:
        default_name = f"数据集 {default_name}"
    outcome = await receive_dataset_upload(
        data_root=settings.data_root,
        upload=archive,
        dataset_name=(dataset_name.strip() or default_name)[:100],
        description=description,
        collector_robot_id=robot["id"],
        collector_robot_type=robot["robot_type"],
        expected_sha256=archive_sha256,
        robot_type_confirmation=robot_type_confirmation,
    )
    if outcome.payload.get("ok"):
        service.finish_upload(
            settings.data_root,
            robot,
            archive_sha256,
            outcome.payload["dataset_id"],
        )
        outcome.payload["collector_robot_id"] = robot["id"]
    else:
        service.fail_upload(
            settings.data_root,
            robot["id"],
            archive_sha256,
            outcome.payload["message"],
        )
    return JSONResponse(status_code=outcome.status_code, content=outcome.payload)


@router.post("/uploads/rebind")
def rebind_duplicate(
    payload: RebindDuplicate,
    authorization: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    robot, error = _authenticate(authorization)
    if error:
        return error
    try:
        result = service.rebind_duplicate(
            settings.data_root,
            robot,
            archive_sha256=payload.archive_sha256,
            dataset_id=payload.dataset_id,
            confirm=payload.confirm,
        )
    except service.CollectorRuleError as exc:
        return _rule_error(exc)
    return JSONResponse(content=result)
