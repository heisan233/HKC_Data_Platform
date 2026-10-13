"""展示实际登记的数据集，并提供跳转到上传与质检页面的入口。"""
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates
from dataset_store import init_store, list_datasets

router = APIRouter()
templates = Jinja2Templates(directory='templates')
init_store()


@router.get('/')
def data_page(request: Request):
    return templates.TemplateResponse(request=request, name='data.html',
                                      context={'datasets': list_datasets()})
