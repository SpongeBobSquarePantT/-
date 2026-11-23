"""安装器工厂：按当前平台选择实现。"""

from typing import Optional

from ..utils.system import current_platform
from .base import InstallerBase, LogCallback
from .linux import LinuxInstaller
from .windows import WindowsInstaller


def create_installer(log_callback: Optional[LogCallback] = None) -> InstallerBase:
    if current_platform() == "windows":
        return WindowsInstaller(log_callback)
    return LinuxInstaller(log_callback)
