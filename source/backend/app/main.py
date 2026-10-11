"""FastAPI 应用入口：生命周期、CORS、统一异常处理、静态挂载、健康检查。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from backend.app import __api_version__
from backend.app.config import PROJECT_ROOT, settings
from backend.app.core import AppError
from backend.app.db import check_db, engine, init_db

logger = logging.getLogger("ecom")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

_STATIC_DIR = PROJECT_ROOT / "backend" / "app" / "static"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # 启动时建表 + 幂等插入默认预警规则；DB 路径可通过 ECOM_DB_PATH 覆盖
    init_db()
    logger.info("数据库就绪: %s", engine.url)
    yield
    engine.dispose()


app = FastAPI(
    title="多平台电商销售日报生成器",
    version=__api_version__,
    lifespan=_lifespan,
)

# CORS：仅开发模式需要（生产同源不开放）
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---- 统一响应包裹与异常处理（规划第 7 节） ----


def ok(data) -> dict:
    """成功响应包裹。"""
    return {"ok": True, "data": data}


def fail(code: str, message: str, detail=None, status_code: int = 500) -> JSONResponse:
    """失败响应包裹。"""
    return JSONResponse(
        status_code=status_code,
        content={"ok": False, "error": {"code": code, "message": message, "detail": detail}},
    )


@app.exception_handler(AppError)
async def _app_error_handler(_request: Request, exc: AppError):
    return fail(exc.code, exc.message, exc.detail, exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(_request: Request, exc: RequestValidationError):
    # 框架层参数校验失败 → VALIDATION_ERROR（422）
    return fail(
        "VALIDATION_ERROR",
        "请求参数校验失败",
        detail=jsonable_encoder(exc.errors()),
        status_code=422,
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception):
    # 完整异常栈只进日志；响应体返回固定文案，避免向客户端
    # 泄露内部细节（文件路径 / SQL 片段 / 依赖库版本等）
    logger.exception("未处理异常 %s %s: %s", request.method, request.url.path, exc)
    return fail(
        "INTERNAL_ERROR",
        "服务器内部错误，详情请查看服务端日志",
        status_code=500,
    )


# ---- 健康检查 ----

@app.get("/api/health")
def health():
    """返回版本、DB 连通性、内置适配器数量。"""
    from backend.app.core.adapter_registry import count_builtin_adapters

    return ok(
        {
            "version": settings.app_version,
            "db": "ok" if check_db() else "error",
            "builtin_adapters": count_builtin_adapters(),
        }
    )


# ---- 业务路由 ----

from backend.app.api import adapters, ai, mock, reports, skus, uploads, weekly  # noqa: E402

app.include_router(adapters.router, prefix="/api")
app.include_router(uploads.router, prefix="/api")
app.include_router(skus.router, prefix="/api")
app.include_router(reports.router, prefix="/api")
app.include_router(weekly.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(mock.router, prefix="/api")

# ---- 前端静态产物挂载（npm run build 输出到 backend/app/static） ----
# 注册在 API 路由之后：/api/* 优先；文件存在则按类型返回，
# 其余路径回退 index.html（Vue history 路由的 SPA fallback）
if _STATIC_DIR.is_dir() and (_STATIC_DIR / "index.html").is_file():

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        candidate = (_STATIC_DIR / full_path).resolve()
        # 防目录穿越：仅允许 static 目录内的文件。
        # 用 is_relative_to 而非 startswith —— 后者会被同前缀的
        # 兄弟路径（如 static_evil）绕过
        if (
            full_path
            and candidate.is_relative_to(_STATIC_DIR.resolve())
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")

else:
    @app.get("/", include_in_schema=False)
    def _root_hint():
        return ok({"hint": "前端未构建。开发模式请访问 http://localhost:5173，"
                           "或执行 cd frontend && npm run build 后重启。"})
