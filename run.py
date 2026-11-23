"""DevBox 一键启动入口（可在任意目录执行）。

用法:
    python run.py              启动服务（默认 8080 端口）
    python run.py --port 9090  使用自定义端口启动
    python run.py --stop       停止占用端口的服务并释放端口
    python run.py --stop --port 9090  停止指定端口的服务
"""

import argparse
import os
import re
import signal
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):
    # 打包后以 exe 所在目录为工作目录，不再依赖源码路径
    PROJECT_ROOT = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(PROJECT_ROOT)
else:
    sys.path.insert(0, PROJECT_ROOT)
    os.chdir(PROJECT_ROOT)

# 用于确认占用端口的进程是否由本工具启动，避免 --stop 误杀无关服务
DEVBOX_MARKERS = ("run.py", "backend.main", "devbox")


def _process_command_line(pid: int) -> str:
    """读取进程命令行（读取失败或权限不足时返回空串）。"""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive", "-Command",
                    f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine',
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            return (result.stdout or "").strip()
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        return raw.replace(b"\x00", b" ").decode(errors="replace").strip()
    except Exception:
        return ""


def _is_devbox_process(pid: int) -> bool:
    """判断 PID 是否为本项目启动的服务（命令行包含 DevBox 特征）。"""
    cmdline = _process_command_line(pid).lower()
    return bool(cmdline) and any(marker in cmdline for marker in DEVBOX_MARKERS)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def find_pids_on_port(port: int) -> list:
    """返回占用指定端口的进程 PID 列表（跨平台）。"""
    pids = []
    if sys.platform == "win32":
        try:
            output = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True, timeout=15
            ).stdout
            pattern = re.compile(
                rf"^\s*TCP\s+.*:{port}\s+.*LISTENING\s+(\d+)\s*$", re.MULTILINE
            )
            # netstat 会为同一进程列出 IPv4/IPv6 两条记录，按 PID 去重
            pids = list(dict.fromkeys(int(m) for m in pattern.findall(output)))
        except Exception:
            pass
    else:
        # Linux / macOS: 优先 lsof，回退 fuser，最后 ss
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"], capture_output=True, text=True, timeout=10
            )
            pids = sorted({int(m) for m in re.findall(r"^\s*(\d+)\s*$", result.stdout, re.MULTILINE)})
        except Exception:
            pids = []
        if not pids:
            try:
                result = subprocess.run(
                    ["fuser", f"{port}/tcp"], capture_output=True, text=True, timeout=10
                )
                # fuser 输出为 PID 列表，排除端口号本身，避免把端口号当 PID
                pids = sorted({int(m) for m in re.findall(r"\d+", result.stdout)} - {port})
            except Exception:
                pids = []
        if not pids:
            try:
                result = subprocess.run(
                    ["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, timeout=10
                )
                # 只解析 users:(("python",pid=1234,fd=6)) 中的 pid=，不要抓所有数字
                pids = sorted({int(m) for m in re.findall(r"pid=(\d+)", result.stdout)})
            except Exception:
                pids = []
    return pids


def stop_port(port: int, force: bool = False) -> bool:
    """停止占用指定端口的进程，返回是否有进程被停止。

    默认只停止确认为本工具启动的进程；force=True 时停止所有占用端口的进程。
    """
    pids = find_pids_on_port(port)
    if not pids:
        print(f"[DevBox] 端口 {port} 没有被占用")
        return False

    stopped = False
    for pid in pids:
        if pid <= 0:
            continue
        if not force and not _is_devbox_process(pid):
            print(
                f"[DevBox] PID={pid} 正在占用端口 {port}，但无法确认它属于 DevBox，已跳过。"
                f"确认可停止后可执行: python run.py --stop --port {port} --force"
            )
            continue
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0:
                    print(
                        f"[DevBox] 停止 PID={pid} 失败: "
                        f"{result.stderr.strip() or '无权限或进程不存在'}"
                    )
                    continue
            else:
                os.kill(pid, signal.SIGTERM)
                for _ in range(25):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.2)
                else:
                    os.kill(pid, signal.SIGKILL)
            print(f"[DevBox] 已停止进程 PID={pid}（端口 {port}）")
            stopped = True
        except Exception as exc:
            print(f"[DevBox] 停止 PID={pid} 失败: {exc}")
    return stopped


def main() -> None:
    parser = argparse.ArgumentParser(description="DevBox 开发环境安装器")
    parser.add_argument("--port", type=int, default=8080, help="服务端口（默认 8080）")
    parser.add_argument(
        "--stop", action="store_true", help="停止占用端口的服务并释放端口"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="配合 --stop：不校验进程归属，停止所有占用该端口的进程",
    )
    args = parser.parse_args()

    if args.stop:
        stop_port(args.port, force=args.force)
        return

    # 启动前检查端口占用
    occupied = find_pids_on_port(args.port)
    if occupied:
        print(
            f"[DevBox] 端口 {args.port} 已被占用 (PID={occupied})，"
            f"请先执行: python run.py --stop --port {args.port}"
        )
        sys.exit(1)

    print(f"[DevBox] 正在启动服务: http://localhost:{args.port}")
    import uvicorn

    if getattr(sys, "frozen", False):
        # PyInstaller 里没有可导入的 "backend.main" 模块字符串，直接传应用对象
        from backend.main import app as asgi_app

        uvicorn.run(asgi_app, host="0.0.0.0", port=args.port)
    else:
        uvicorn.run(
            "backend.main:app",
            host="0.0.0.0",
            port=args.port,
        )


if __name__ == "__main__":
    main()
