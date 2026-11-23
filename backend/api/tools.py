"""工具清单 API。"""

import asyncio

from fastapi import APIRouter, Query

from ..config.loader import load_tools_config
from ..installers.factory import create_installer
from ..installers.windows import WindowsInstaller
from ..utils.system import current_platform, detect_package_manager

router = APIRouter(prefix="/api", tags=["tools"])

_CHECK_TIMEOUT = 15

# 检测状态：pending / running / ready
_detection = {"status": "pending", "installed": {}}
_detection_task = None


async def _run_detection() -> None:
    """后台检测所有工具的安装状态（启动时预检 + 手动刷新）。"""
    if _detection["status"] == "running":
        return
    _detection["status"] = "running"
    try:
        config = load_tools_config()
        installer = create_installer()
        # 重新检测前丢弃上一轮的探测缓存，否则外部安装/卸载的软件看不到。
        installer.invalidate_caches()

        # 预热 JDK 探测，避免多个工具重复触发全盘扫描
        if isinstance(installer, WindowsInstaller):
            try:
                await asyncio.wait_for(installer._java_versions(), timeout=_CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                pass

        async def check_one(tool: dict) -> tuple:
            try:
                return tool["id"], await asyncio.wait_for(
                    installer.check_installed(tool), timeout=_CHECK_TIMEOUT
                )
            except (asyncio.TimeoutError, Exception):
                return tool["id"], False

        results = await asyncio.gather(*(check_one(t) for t in config["tools"]))
        _detection["installed"].update(dict(results))
    finally:
        _detection["status"] = "ready"


def start_detection() -> None:
    """启动后台检测任务（不阻塞启动）。"""
    global _detection_task
    # 存下句柄，否则启动检测未完成时收到 refresh 会再起一轮并重复扫描
    _detection_task = asyncio.create_task(_run_detection())


async def ensure_detected(refresh: bool = False) -> None:
    """普通请求直接读缓存；refresh 时在后台重新检测，页面轮询等待结果。"""
    global _detection_task
    if not refresh:
        return
    # 已有检测任务在跑就不再重复调度；本次请求立即返回，前端轮询拿结果。
    if _detection_task is not None and not _detection_task.done():
        return
    _detection["status"] = "pending"
    _detection_task = asyncio.create_task(_run_detection())


def mark_installed(tool_id: str, installed: bool = True) -> None:
    """单个工具安装状态更新（安装完成后调用）。"""
    _detection["installed"][tool_id] = installed


@router.get("/tools")
async def get_tools(refresh: bool = Query(False)):
    """返回分类 + 工具清单（含安装状态），优先读缓存。"""
    await ensure_detected(refresh=refresh)
    config = load_tools_config()
    installed = _detection["installed"]
    tools = [dict(t, installed=installed.get(t["id"], False)) for t in config["tools"]]
    platform = current_platform()
    package_manager = "winget" if platform == "windows" else detect_package_manager()
    return {
        "platform": platform,
        "package_manager": package_manager,
        "categories": config["categories"],
        "tools": tools,
        "detection": _detection["status"],
    }
