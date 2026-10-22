"""FastAPI 应用的总入口。

这个文件只负责把静态文件和各业务模块的 HTTP 路由组装成一个应用，不在这里
编写数据集扫描、质检、格式转换等具体业务逻辑。Uvicorn 启动时导入的是文件
末尾的 ``app`` 对象。
"""

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.paths import STATIC_DIR
from app.modules.annotation import router as annotation_router
from app.modules.auth import router as auth_router
from app.modules.auth.middleware import LoginRequiredMiddleware
from app.modules.dashboard import router as dashboard_router
from app.modules.datasets import router as datasets_router
from app.modules.export import router as export_router
from app.modules.ingestion import router as ingestion_router
from app.modules.processing import router as processing_router
from app.modules.quality import router as quality_router
from app.modules.robots import router as robots_router
from app.modules.semantics import router as semantics_router
from app.modules.system import router as system_router
from app.modules.training import router as training_router


def create_app() -> FastAPI:
    """创建平台应用，并把各模块提供的路由注册到对应网址。"""
    application = FastAPI(title="HKC 数采平台")
    application.add_middleware(LoginRequiredMiddleware)

    # 把 app/static 暴露为 /static，供浏览器加载 CSS、JavaScript 和图片。
    application.mount(
        "/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="static",
    )

    application.include_router(auth_router.router, tags=["用户登录"])

    # include_router 只是在分配网址。例如 datasets 模块内的 "/"，加上这里的
    # prefix="/data" 后，浏览器最终访问的是 "/data/"。
    application.include_router(
        dashboard_router.router,
        prefix="/dashboard",
        tags=["工作台"],
    )
    application.include_router(
        semantics_router.router,
        prefix="/semantics",
        tags=["数据定义"],
    )
    application.include_router(
        robots_router.router,
        prefix="/robots",
        tags=["机器人管理"],
    )
    application.include_router(
        datasets_router.router,
        prefix="/data",
        tags=["数据管理"],
    )
    application.include_router(
        ingestion_router.router,
        prefix="/ingestion",
        tags=["原始数据集上传"],
    )
    application.include_router(
        processing_router.router,
        prefix="/preprocess",
        tags=["数据预处理"],
    )
    application.include_router(
        quality_router.router,
        prefix="/qc",
        tags=["数据质检"],
    )
    application.include_router(
        annotation_router.router,
        prefix="/annotation",
        tags=["数据标注"],
    )
    application.include_router(
        export_router.router,
        prefix="/export",
        tags=["数据集导出"],
    )
    application.include_router(
        training_router.router,
        prefix="/training",
        tags=["模型训练"],
    )
    application.include_router(
        system_router.router,
        prefix="/system",
        tags=["系统管理"],
    )

    @application.get("/healthz", include_in_schema=False)
    async def healthcheck() -> dict[str, str]:
        """Public liveness probe; it exposes no platform or user data."""
        return {"status": "ok"}

    # 访问网站根地址时，不渲染新页面，直接跳转到工作台。
    @application.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    return application


app = create_app()
