from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from routers import (
    dashboard,
    semantics,
    robots,
    scenes,
    data,
    preprocess,
    qc,
    annotation,
    export,
    training,
    system,
)

app = FastAPI(title="数采平台")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(dashboard.router, prefix="/dashboard", tags=["工作台"])
app.include_router(semantics.router, prefix="/semantics", tags=["数据定义"])
app.include_router(robots.router, prefix="/robots", tags=["机器人管理"])
app.include_router(scenes.router, prefix="/scenes", tags=["情景管理"])
app.include_router(data.router, prefix="/data", tags=["数据管理"])
app.include_router(preprocess.router, prefix="/preprocess", tags=["数据预处理"])
app.include_router(qc.router, prefix="/qc", tags=["数据质检"])
app.include_router(annotation.router, prefix="/annotation", tags=["数据标注"])
app.include_router(export.router, prefix="/export", tags=["数据集导出"])
app.include_router(training.router, prefix="/training", tags=["模型训练"])
app.include_router(system.router, prefix="/system", tags=["系统管理"])


@app.get("/")
async def root():
    return RedirectResponse("/dashboard/")