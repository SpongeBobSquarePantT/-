"""资源与数据目录：兼容开发运行和 PyInstaller 打包运行。"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_path(*parts: str) -> Path:
    """只读资源：开发时取项目根，打包后取 PyInstaller 解包目录。"""
    base = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
    return base.joinpath(*parts)


def data_dir() -> Path:
    """可写目录：打包后为 exe 所在目录，开发时为项目根。"""
    return Path(sys.executable).resolve().parent if is_frozen() else _PROJECT_ROOT
