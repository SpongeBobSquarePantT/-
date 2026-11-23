"""DevBox 后端入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import install, search, tools
from .config.loader import load_tools_config
from .utils.paths import resource_path

FRONTEND_DIR = resource_path("frontend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 配置错误直接启动失败并给出明确原因，而不是后台静默跑成空清单
    try:
        load_tools_config()
    except ValueError as exc:
        raise RuntimeError(f"tools.json 配置错误: {exc}") from exc
    tools.start_detection()
    yield


app = FastAPI(
    title="DevBox",
    description="跨平台开发环境图形化安装工具",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(tools.router)
app.include_router(search.router)
app.include_router(install.router)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")
