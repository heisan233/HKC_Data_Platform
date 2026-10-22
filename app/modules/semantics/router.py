from fastapi import APIRouter, HTTPException, Query, Request

from app.core.config import settings
from app.modules.semantics import service
from app.modules.semantics.schemas import DefinitionCreate, DefinitionUpdate
from app.web import templates

router = APIRouter()


@router.get("/")
async def semantics_page(
    request: Request,
    q: str = "",
    category: str = Query(default="all", pattern="^(all|scene|task|action|object)$"),
    status: str = Query(default="all", pattern="^(all|active|inactive)$"),
):
    selected_category = None if category == "all" else category
    selected_status = None if status == "all" else status
    definitions = service.list_definitions(settings.data_root, selected_category, selected_status)
    term = q.strip().casefold()
    if term:
        definitions = [
            item
            for item in definitions
            if term
            in " ".join(
                [
                    item["id"],
                    item["code"],
                    item["name"],
                    item["description"],
                    " ".join(item["examples"]),
                ]
            ).casefold()
        ]
    all_definitions = service.list_definitions(settings.data_root)
    counts = {
        "all": len(all_definitions),
        "active": sum(item["status"] == "active" for item in all_definitions),
        "inactive": sum(item["status"] == "inactive" for item in all_definitions),
        "categories": {
            key: sum(item["category"] == key for item in all_definitions)
            for key in service.CATEGORY_INFO
        },
    }
    return templates.TemplateResponse(
        request=request,
        name="semantics.html",
        context={
            "definitions": definitions,
            "categories": service.CATEGORY_INFO,
            "status_labels": service.STATUS_LABELS,
            "structure": service.LEROBOT_STRUCTURE,
            "counts": counts,
            "query": q.strip(),
            "category_filter": category,
            "status_filter": status,
        },
    )


@router.get("/api/definitions")
def definitions_api(category: str | None = None, status: str | None = None) -> list[dict]:
    try:
        return service.list_definitions(settings.data_root, category, status)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/definitions", status_code=201)
def create_definition(payload: DefinitionCreate) -> dict:
    try:
        return service.create_definition(settings.data_root, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/api/definitions/{definition_id}")
def update_definition(definition_id: str, payload: DefinitionUpdate) -> dict:
    try:
        definition = service.update_definition(settings.data_root, definition_id, payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if definition is None:
        raise HTTPException(404, "数据定义不存在")
    return definition


@router.post("/api/definitions/{definition_id}/deactivate")
def deactivate_definition(definition_id: str) -> dict:
    definition = service.set_definition_status(settings.data_root, definition_id, "inactive")
    if definition is None:
        raise HTTPException(404, "数据定义不存在")
    return definition


@router.post("/api/definitions/{definition_id}/activate")
def activate_definition(definition_id: str) -> dict:
    definition = service.set_definition_status(settings.data_root, definition_id, "active")
    if definition is None:
        raise HTTPException(404, "数据定义不存在")
    return definition
