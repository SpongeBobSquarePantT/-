"""系统与平台检测工具。"""

import platform
import shutil
from typing import Optional


def current_platform() -> str:
    """返回当前平台: windows / linux / macos / other。"""
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system.startswith("linux"):
        return "linux"
    if system.startswith("darwin"):
        return "macos"
    return system


def detect_package_manager() -> Optional[str]:
    """探测 Linux 下可用的包管理器，未找到返回 None。"""
    aliases = {
        "apt-get": "apt",
        "apt": "apt",
        "dnf": "dnf",
        "yum": "yum",
        "pacman": "pacman",
        "zypper": "zypper",
        "snap": "snap",
    }
    for executable in aliases:
        if shutil.which(executable):
            return aliases[executable]
    return None
