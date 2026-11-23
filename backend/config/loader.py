"""工具清单加载器。"""

import json

from ..utils.paths import resource_path

TOOLS_FILE = resource_path("backend", "config", "tools.json")

_REQUIRED_TOOL_KEYS = ("id", "name", "category", "description")


def _validate(config: dict) -> None:
    """校验配置结构，出错时抛出带具体位置的 ValueError。"""
    if not isinstance(config.get("categories"), list) or not isinstance(config.get("tools"), list):
        raise ValueError("tools.json 必须包含 categories 与 tools 两个数组")

    category_ids = set()
    for cat in config["categories"]:
        if not cat.get("id") or not cat.get("name"):
            raise ValueError(f"分类缺少 id 或 name: {cat}")
        if cat["id"] in category_ids:
            raise ValueError(f"分类 id 重复: {cat['id']}")
        category_ids.add(cat["id"])

    tool_ids = set()
    for tool in config["tools"]:
        missing = [key for key in _REQUIRED_TOOL_KEYS if not tool.get(key)]
        if missing:
            raise ValueError(f"工具 {tool.get('id', '?')} 缺少字段: {', '.join(missing)}")
        if tool["id"] in tool_ids:
            raise ValueError(f"工具 id 重复: {tool['id']}")
        tool_ids.add(tool["id"])
        if tool["category"] not in category_ids:
            raise ValueError(f"工具 {tool['id']} 的分类不存在: {tool['category']}")
        if not tool.get("windows") and not tool.get("linux"):
            raise ValueError(f"工具 {tool['id']} 未定义 windows/linux 安装方式")


def load_tools_config() -> dict:
    """读取、校验并返回 tools.json 内容。"""
    with open(TOOLS_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)
    _validate(config)
    return config
