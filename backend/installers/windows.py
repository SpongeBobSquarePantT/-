"""Windows 安装引擎：基于 PATH + 注册表 + winget + 版本探测。"""

import asyncio
import glob
import os
import re
import shutil
import subprocess
import time
from typing import List, Set

from .base import InstallerBase

WINGET_INSTALL_ARGS = ["-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]


def build_winget_install_command(package: str) -> List[str]:
    """winget 安装命令（自定义安装在此之上追加版本与路径参数）。"""
    return ["winget", "install", "--id", package, *WINGET_INSTALL_ARGS]


def _path_exists(pattern: str) -> bool:
    """展开环境变量并用通配符检测路径是否存在。"""
    expanded = os.path.expandvars(pattern)
    return bool(glob.glob(expanded))


def _registry_display_names() -> Set[str]:
    """读取 Windows 卸载注册表中的已安装软件名称。"""
    import winreg

    names: Set[str] = set()
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root, sub_key in roots:
        try:
            with winreg.OpenKey(root, sub_key) as key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                            name, _ = winreg.QueryValueEx(sub, "DisplayName")
                            if name:
                                names.add(name.lower())
                    except OSError:
                        pass
        except OSError:
            pass
    return names


def _discover_java_homes() -> Set[str]:
    """发现本机所有 JDK 安装目录（PATH/JAVA_HOME/注册表/常见路径）。"""
    homes: Set[str] = set()

    java_home = os.environ.get("JAVA_HOME")
    if java_home and os.path.isdir(java_home):
        homes.add(java_home)

    which_java = shutil.which("java")
    if which_java:
        home = os.path.dirname(os.path.dirname(which_java))
        homes.add(home)

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Development Kit") as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                        home, _ = winreg.QueryValueEx(sub, "JavaHome")
                        if home and os.path.isdir(home):
                            homes.add(home)
                except OSError:
                    pass
    except OSError:
        pass

    patterns = [
        r"%ProgramFiles%\Java\jdk*",
        r"%ProgramFiles%\Eclipse Adoptium\*",
        r"%ProgramFiles%\Microsoft\jdk*",
        r"%ProgramFiles%\Zulu\jdk*",
        r"%USERPROFILE%\.jdks\*",
        r"%LOCALAPPDATA%\Programs\Eclipse Adoptium\*",
    ]
    for pattern in patterns:
        for match in glob.glob(os.path.expandvars(pattern)):
            if os.path.isdir(match) and os.path.isdir(os.path.join(match, "bin")):
                homes.add(match)

    return homes


def _java_versions_from_home(home: str) -> List[str]:
    """同步探测单个 JDK 目录的主版本号。"""
    java = os.path.join(home, "bin", "java.exe")
    if not os.path.isfile(java):
        return []
    try:
        result = subprocess.run(
            [java, "-version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        output = (result.stdout + result.stderr).lower()
        match = re.search(r'version\s+"([0-9._]+)"', output)
        if not match:
            return []
        raw = match.group(1)
        parts = raw.split(".")
        if parts[0] == "1" and len(parts) > 1:
            return [parts[1]]
        return [parts[0]]
    except Exception:
        return []


class WindowsInstaller(InstallerBase):
    platform = "windows"
    # 0x8A150061 = APPINSTALLER_CLI_ERROR_PACKAGE_ALREADY_INSTALLED
    already_installed_codes = (0x8A150061,)

    _winget_cache: dict = {"ts": 0.0, "text": None}
    _registry_cache: dict = {"ts": 0.0, "names": None}
    _java_cache: dict = {"ts": 0.0, "versions": None}
    _java_lock = None
    _winget_lock = None
    _registry_lock = None

    def __init__(self, log_callback=None) -> None:
        super().__init__(log_callback)
        if WindowsInstaller._java_lock is None:
            WindowsInstaller._java_lock = asyncio.Lock()
        if WindowsInstaller._winget_lock is None:
            WindowsInstaller._winget_lock = asyncio.Lock()
        if WindowsInstaller._registry_lock is None:
            WindowsInstaller._registry_lock = asyncio.Lock()

    def invalidate_caches(self) -> None:
        """清空探测缓存（安装完成或用户手动刷新时调用）。"""
        WindowsInstaller._winget_cache.update(ts=0.0, text=None)
        WindowsInstaller._registry_cache.update(ts=0.0, names=None)
        WindowsInstaller._java_cache.update(ts=0.0, versions=None)

    async def _winget_installed_text(self) -> str:
        """获取 winget list 输出（300 秒缓存 + 锁），winget 不可用时返回空串。"""
        now = time.time()
        if now - self._winget_cache["ts"] < 300 and self._winget_cache["text"] is not None:
            return self._winget_cache["text"]

        async with WindowsInstaller._winget_lock:
            now = time.time()
            if now - self._winget_cache["ts"] < 300 and self._winget_cache["text"] is not None:
                return self._winget_cache["text"]
            text = ""
            try:
                proc = await asyncio.create_subprocess_exec(
                    "winget", "list", "--accept-source-agreements",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                output, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
                text = output.decode("utf-8", errors="replace").lower()
            except (asyncio.TimeoutError, OSError, Exception):
                text = ""
            self._winget_cache.update(ts=now, text=text)
            return text

    async def _registry_names(self) -> Set[str]:
        """获取注册表已安装软件名（60 秒缓存）。

        注册表遍历是同步 IO，放到线程池执行，避免阻塞事件循环。
        """
        now = time.time()
        cached = self._registry_cache["names"]
        if cached is not None and now - self._registry_cache["ts"] < 60:
            return cached

        async with WindowsInstaller._registry_lock:
            now = time.time()
            cached = self._registry_cache["names"]
            if cached is not None and now - self._registry_cache["ts"] < 60:
                return cached
            names = await asyncio.to_thread(_registry_display_names)
            self._registry_cache.update(ts=now, names=names)
            return names

    async def _java_versions(self) -> Set[str]:
        """扫描本机所有 JDK，返回已安装主版本号集合（60 秒缓存）。"""
        now = time.time()
        cached = self._java_cache["versions"]
        if cached is not None and now - self._java_cache["ts"] < 60:
            return cached

        async with WindowsInstaller._java_lock:
            now = time.time()
            cached = self._java_cache["versions"]
            if cached is not None and now - self._java_cache["ts"] < 60:
                return cached
            # 目录扫描也是同步 IO，放到线程池执行。
            homes = await asyncio.to_thread(_discover_java_homes)
            versions: Set[str] = set()
            for home in homes:
                found = await asyncio.to_thread(_java_versions_from_home, home)
                versions.update(found)
            self._java_cache.update(ts=now, versions=versions)
            return versions

    async def check_installed(self, tool: dict) -> bool:
        """检测顺序: JDK 版本 -> PATH -> 注册表 -> 安装路径 -> winget -> check 命令。"""
        spec = tool.get("windows") or {}

        java_version = spec.get("java_version")
        if java_version:
            versions = await self._java_versions()
            if java_version in versions:
                return True

        for executable in spec.get("executables") or []:
            if shutil.which(executable):
                return True

        registry_keywords = spec.get("registry") or []
        if registry_keywords:
            names = await self._registry_names()
            lowered = [keyword.lower() for keyword in registry_keywords]
            if any(keyword in name for name in names for keyword in lowered):
                return True

        for pattern in spec.get("paths") or []:
            if _path_exists(pattern):
                return True

        package = spec.get("package")
        if package:
            text = await self._winget_installed_text()
            if text and package.lower() in text:
                return True

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
        spec = tool.get("windows") or {}
        package = spec.get("package")
        if not package:
            return []
        return build_winget_install_command(package)

    def get_check_command(self, tool: dict) -> str:
        spec = tool.get("windows") or {}
        return spec.get("check") or tool.get("check") or ""
