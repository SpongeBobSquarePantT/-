"""数据模型定义。"""

from typing import List

from pydantic import BaseModel


class InstallRequest(BaseModel):
    tool_ids: List[str]


class CustomInstallRequest(BaseModel):
    """自定义包安装请求（搜索安装）。"""

    package_id: str
    name: str = ""
    version: str = ""
    install_path: str = ""
    source: str = ""
