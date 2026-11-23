"""安装任务与实时日志 API。"""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..config.loader import load_tools_config
from ..installers.base import decode_output
from ..installers.factory import create_installer
from ..installers.linux import build_install_command
from ..installers.windows import build_winget_install_command
from ..models.tool import CustomInstallRequest, InstallRequest
from ..utils.paths import data_dir
from ..utils.system import current_platform, detect_package_manager
from . import tools as tools_api

DOWNLOAD_DIR = os.environ.get("DEVBOX_DOWNLOAD_DIR") or str(data_dir() / "downloads")

router = APIRouter(prefix="/api", tags=["install"])

MAX_TASKS = 20        # 保留的最近任务数，超出后清理已结束的任务
MAX_LOG_LINES = 2000  # 单任务日志上限，超出丢弃最早的

# 串行执行安装任务，避免多个 winget/包管理器争抢缓存和锁文件
_install_lock = asyncio.Lock()


class InstallManager:
    """管理安装任务并广播日志到所有 WebSocket 订阅者。"""

    def __init__(self) -> None:
        self.tasks: Dict[str, dict] = {}
        self.handles: Dict[str, "asyncio.Task"] = {}
        self._subscribers: List[WebSocket] = []

    def add_subscriber(self, websocket: WebSocket) -> None:
        self._subscribers.append(websocket)

    def remove_subscriber(self, websocket: WebSocket) -> None:
        if websocket in self._subscribers:
            self._subscribers.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        for websocket in list(self._subscribers):
            try:
                await websocket.send_json(message)
            except Exception:
                self.remove_subscriber(websocket)

    def create_task(self, **fields) -> dict:
        task_id = uuid.uuid4().hex[:10]
        task = {"task_id": task_id, "status": "queued", "results": [], "logs": []}
        task.update(fields)
        self.tasks[task_id] = task
        self._prune()
        return task

    def _prune(self) -> None:
        for task_id in list(self.tasks):
            if len(self.tasks) <= MAX_TASKS:
                break
            if self.tasks[task_id]["status"] == "done":
                del self.tasks[task_id]
                self.handles.pop(task_id, None)

    async def emit(self, task_id: str, level: str, message: str, event: str = "") -> None:
        record = {"task_id": task_id, "level": level, "message": message}
        if event:
            record["event"] = event
        task = self.tasks.get(task_id)
        if task:
            task["logs"].append(record)
            if len(task["logs"]) > MAX_LOG_LINES:
                del task["logs"][: len(task["logs"]) - MAX_LOG_LINES]
        await self.broadcast(record)


manager = InstallManager()


@router.post("/install")
async def start_install(request: InstallRequest):
    task = manager.create_task(tool_ids=request.tool_ids)
    manager.handles[task["task_id"]] = asyncio.create_task(
        _run_install(task["task_id"], request.tool_ids)
    )
    return {"task_id": task["task_id"], "status": "queued"}


async def _run_install(task_id: str, tool_ids: List[str]) -> None:
    async with _install_lock:
        task = manager.tasks[task_id]
        # 排队期间被取消（handle.cancel 未及时生效）时不要执行
        if task.get("cancelled") or task["status"] == "done":
            return
        task["status"] = "running"
        try:
            config = load_tools_config()
            tools_by_id = {tool["id"]: tool for tool in config["tools"]}
            installer = create_installer()
            installer.set_log_callback(
                lambda level, message: manager.emit(task_id, level, message)
            )

            for tool_id in tool_ids:
                if task.get("cancelled"):
                    break
                tool = tools_by_id.get(tool_id)
                if not tool:
                    await manager.emit(task_id, "error", f"未知工具: {tool_id}")
                    task["results"].append(
                        {"tool_id": tool_id, "name": tool_id, "status": "failed", "message": "未知工具"}
                    )
                    continue
                try:
                    if await installer.check_installed(tool):
                        tools_api.mark_installed(tool["id"], True)
                        await manager.emit(task_id, "success", f"已安装，跳过: {tool['name']}")
                        task["results"].append(
                            {"tool_id": tool_id, "name": tool["name"], "status": "skipped"}
                        )
                        continue
                    ok = await installer.install(tool)
                    if ok:
                        tools_api.mark_installed(tool["id"], True)
                    status = "success" if ok else "failed"
                    message = "安装成功" if ok else "安装失败"
                    await manager.emit(task_id, "success" if ok else "error", f"{tool['name']}: {message}")
                    task["results"].append(
                        {"tool_id": tool_id, "name": tool["name"], "status": status, "message": message}
                    )
                except Exception as exc:
                    await manager.emit(task_id, "error", f"{tool['name']} 安装异常: {exc}")
                    task["results"].append(
                        {"tool_id": tool_id, "name": tool["name"], "status": "failed", "message": str(exc)}
                    )
        finally:
            task["status"] = "done"
            level = "warn" if task.get("cancelled") else "info"
            message = "任务已取消" if task.get("cancelled") else "全部安装任务执行完毕"
            await manager.emit(task_id, level, message, event="done")


@router.post("/install/package")
async def install_package(request: CustomInstallRequest):
    """按包 ID 安装自定义软件，支持指定版本与安装路径。"""
    task = manager.create_task(package=request.package_id)
    manager.handles[task["task_id"]] = asyncio.create_task(
        _run_package_install(task["task_id"], request)
    )
    return {"task_id": task["task_id"], "status": "queued"}


async def _run_package_install(task_id: str, request: CustomInstallRequest) -> None:
    async with _install_lock:
        task = manager.tasks[task_id]
        # 排队期间被取消（handle.cancel 未及时生效）时不要执行
        if task.get("cancelled") or task["status"] == "done":
            return
        task["status"] = "running"
        display_name = request.name or request.package_id
        try:
            await manager.emit(task_id, "info", f"开始安装: {display_name} ({request.package_id})")
            if request.version:
                await manager.emit(task_id, "info", f"指定版本: {request.version}")
            if request.install_path:
                await manager.emit(task_id, "info", f"安装路径: {request.install_path}")

            command = _build_custom_install_command(request)
            if not command:
                await manager.emit(task_id, "error", "当前平台不支持该安装方式")
                task["results"].append(
                    {"tool_id": request.package_id, "name": display_name, "status": "failed", "message": "不支持"}
                )
                return

            await manager.emit(task_id, "output", "执行命令: " + " ".join(command))
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                raw_line = await proc.stdout.readline()
                if not raw_line:
                    break
                line = decode_output(raw_line).rstrip()
                if line:
                    await manager.emit(task_id, "output", line)
            return_code = await proc.wait()
            ok = return_code == 0
            status = "success" if ok else "failed"
            message = "安装成功" if ok else f"安装失败 (退出码 {return_code})"
            await manager.emit(task_id, "success" if ok else "error", f"{display_name}: {message}")
            if current_platform() == "windows":
                saved = await asyncio.to_thread(_save_winget_installer, request.package_id)
                if saved:
                    await manager.emit(task_id, "info", f"安装包已保留到: {saved}")
                else:
                    await manager.emit(task_id, "info", f"安装包下载目录: {DOWNLOAD_DIR}")
            task["results"].append(
                {"tool_id": request.package_id, "name": display_name, "status": status, "message": message}
            )
        except Exception as exc:
            await manager.emit(task_id, "error", f"{display_name} 安装异常: {exc}")
            task["results"].append(
                {"tool_id": request.package_id, "name": display_name, "status": "failed", "message": str(exc)}
            )
        finally:
            task["status"] = "done"
            await manager.emit(task_id, "info", "全部安装任务执行完毕", event="done")





def _ensure_winget_download_dir() -> None:
    """将 winget 全局下载目录指向 DOWNLOAD_DIR（新旧两个 settings 位置都写）。"""
    local = os.environ.get("LOCALAPPDATA", "")
    settings_paths = [
        os.path.join(local, "Microsoft", "WinGet", "Settings", "settings.json"),
        os.path.join(
            local, "Packages", "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe",
            "LocalState", "settings.json",
        ),
    ]
    for settings_path in settings_paths:
        try:
            data = {}
            if os.path.exists(settings_path):
                try:
                    data = json.loads(Path(settings_path).read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            behavior = data.setdefault("installBehavior", {})
            behavior["downloadPath"] = DOWNLOAD_DIR
            os.makedirs(os.path.dirname(settings_path), exist_ok=True)
            Path(settings_path).write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass




def _save_winget_installer(package_id: str) -> str:
    """把 winget 下载缓存中的安装包移动到下载目录（移动而非复制，不占双份空间）。"""
    try:
        local = os.environ.get("LOCALAPPDATA", "")
        cache_root = os.path.join(local, "Temp", "WinGet")
        if not os.path.isdir(cache_root):
            return ""
        installer = None
        for entry in os.listdir(cache_root):
            folder = os.path.join(cache_root, entry)
            if not os.path.isdir(folder) or not entry.lower().startswith(package_id.lower()):
                continue
            for name in os.listdir(folder):
                if name.lower().endswith((".exe", ".msi", ".msix", ".msixbundle", ".appx", ".appxbundle", ".zip")):
                    installer = os.path.join(folder, name)
                    break
            if installer:
                break
        if not installer:
            return ""
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        target = os.path.join(DOWNLOAD_DIR, os.path.basename(installer))
        stem, ext = os.path.splitext(target)
        counter = 1
        while os.path.exists(target):
            target = f"{stem}_{counter}{ext}"
            counter += 1
        shutil.move(installer, target)
        return target
    except Exception:
        return ""


def _build_custom_install_command(request: CustomInstallRequest) -> List[str]:
    """根据平台与请求构造安装命令。"""
    if current_platform() == "windows":
        source = (request.source or "").lower()
        if source == "choco":
            return ["choco", "install", request.package_id, "-y", "--no-progress"]
        if source == "scoop":
            return ["scoop", "install", request.package_id]
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        _ensure_winget_download_dir()
        cmd = build_winget_install_command(request.package_id)
        if request.version:
            cmd += ["--version", request.version]
        if request.install_path:
            cmd += ["--location", request.install_path]
        return cmd

    # Linux
    manager = detect_package_manager() or ""
    if not manager:
        return []
    package = request.package_id
    if request.version:
        package = f"{package}={request.version}"
    return build_install_command(manager, package)


@router.get("/status/{task_id}")
async def get_status(task_id: str):
    task = manager.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.delete("/status/{task_id}")
async def cancel_task(task_id: str):
    """取消排队中的任务；运行中的工具安装会在当前工具结束后停止。"""
    task = manager.tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task["status"] == "done":
        return {"task_id": task_id, "status": "done"}
    if task["status"] == "running":
        if "package" in task:
            raise HTTPException(status_code=409, detail="安装已开始，无法中途取消")
        task["cancelled"] = True
        await manager.emit(task_id, "warn", "已请求取消，当前工具安装结束后停止")
        return {"task_id": task_id, "status": "cancelling"}
    handle = manager.handles.get(task_id)
    if handle and not handle.done():
        handle.cancel()
    task["status"] = "done"
    await manager.emit(task_id, "warn", "任务已取消", event="done")
    return {"task_id": task_id, "status": "done"}


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket, task_id: str = ""):
    await websocket.accept()
    manager.add_subscriber(websocket)
    try:
        # 连接时补发该任务已有的日志，前端断线重连不会丢历史
        task = manager.tasks.get(task_id) if task_id else None
        for record in list(task["logs"]) if task else []:
            await websocket.send_json(record)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.remove_subscriber(websocket)
