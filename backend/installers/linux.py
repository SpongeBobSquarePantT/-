"""Linux 安装引擎：自动探测并调用系统包管理器。"""

import asyncio
from typing import List, Optional

from ..utils.system import detect_package_manager
from .base import InstallerBase

# 包管理器 -> 安装命令前缀，自定义安装与预设安装共用，避免两处维护
MANAGER_INSTALL_COMMANDS = {
    "apt": ["sudo", "apt-get", "install", "-y"],
    "dnf": ["sudo", "dnf", "install", "-y"],
    "yum": ["sudo", "yum", "install", "-y"],
    "pacman": ["sudo", "pacman", "-S", "--noconfirm"],
    "zypper": ["sudo", "zypper", "install", "-y"],
    "snap": ["sudo", "snap", "install"],
}


def build_install_command(manager: str, package: str) -> List[str]:
    """按包管理器组装安装命令，未知管理器返回空列表。"""
    base = MANAGER_INSTALL_COMMANDS.get(manager)
    return base + [package] if base else []


class LinuxInstaller(InstallerBase):
    platform = "linux"

    def __init__(self, log_callback=None) -> None:
        super().__init__(log_callback)
        self.package_manager: Optional[str] = detect_package_manager()

    def _resolve_package(self, tool: dict) -> Optional[str]:
        spec = tool.get("linux") or {}
        if self.package_manager and spec.get(self.package_manager):
            return spec[self.package_manager]
        return spec.get("package")

    def _query_command(self, package: str) -> List[str]:
        """返回包管理器的已安装查询命令，空列表表示不支持。"""
        manager = self.package_manager or ""
        commands = {
            "apt": ["dpkg-query", "-W", "-f=${Status}", package],
            "dnf": ["rpm", "-q", package],
            "yum": ["rpm", "-q", package],
            "pacman": ["pacman", "-Q", package],
            "zypper": ["zypper", "search", "--installed-only", package],
            "snap": ["snap", "list", package],
        }
        return commands.get(manager, [])

    async def check_installed(self, tool: dict) -> bool:
        """优先用包管理器查询，其次用 check 命令兜底。"""
        package = self._resolve_package(tool)
        if package:
            query = self._query_command(package)
            if query:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        *query,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    output, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                    if proc.returncode == 0:
                        # dpkg-query 对「已卸载但残留配置」的包也返回 0，必须确认状态是真正已安装
                        if self.package_manager != "apt" or b"install ok installed" in output:
                            return True
                except (asyncio.TimeoutError, OSError):
                    pass

        check = self.get_check_command(tool)
        if check:
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
        return False

    def get_install_command(self, tool: dict) -> List[str]:
        package = self._resolve_package(tool)
        if not package:
            return []
        return build_install_command(self.package_manager or "", package)

    def get_check_command(self, tool: dict) -> str:
        spec = tool.get("linux") or {}
        return spec.get("check") or tool.get("check") or ""
