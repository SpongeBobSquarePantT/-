"""安装器基类：定义安装、检测、日志的统一接口。"""

import asyncio
import locale
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

LogCallback = Callable[[str, str], None]


def decode_output(raw: bytes) -> str:
    """按 UTF-8 解码子进程输出，失败时退回系统本地编码（中文 Windows 常见 GBK）。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


class InstallerBase(ABC):
    """平台安装器抽象基类。"""

    platform: str = ""
    # winget 的「已安装」等退出码，遇到时按成功处理（子类按需覆盖）
    already_installed_codes: tuple = ()

    def __init__(self, log_callback: Optional[LogCallback] = None) -> None:
        self._log_callback = log_callback

    def set_log_callback(self, callback: LogCallback) -> None:
        self._log_callback = callback

    def invalidate_caches(self) -> None:
        """丢弃已安装状态探测缓存，下次检测重新读取系统状态。

        基类没有缓存，子类按需覆盖。
        """

    async def _log(self, level: str, message: str) -> None:
        if self._log_callback:
            await self._log_callback(level, message)

    @abstractmethod
    def get_install_command(self, tool: dict) -> List[str]:
        """根据工具定义返回安装命令列表，空列表表示当前平台不支持。"""

    def get_check_command(self, tool: dict) -> str:
        """返回检测是否已安装的命令，默认取根级 check 字段。"""
        return tool.get("check") or ""

    async def check_installed(self, tool: dict) -> bool:
        """执行检测命令，返回是否已安装。"""
        check = self.get_check_command(tool)
        if not check:
            return False
        try:
            proc = await asyncio.create_subprocess_shell(
                check,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return_code = await asyncio.wait_for(proc.wait(), timeout=10)
            return return_code == 0
        except (asyncio.TimeoutError, OSError):
            return False

    async def install(self, tool: dict) -> bool:
        """安装指定工具，成功返回 True。"""
        command = self.get_install_command(tool)
        if not command:
            await self._log("warn", f"{tool['name']} 不支持当前平台，已跳过")
            return False

        await self._log("info", f"开始安装: {tool['name']}")
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
                await self._log("output", line)
        return_code = await proc.wait()
        await self._log("info", f"{tool['name']} 安装进程退出码: {return_code}")
        if return_code in self.already_installed_codes:
            await self._log("info", f"{tool['name']} 已安装，无需重复安装")
            return True
        return return_code == 0
