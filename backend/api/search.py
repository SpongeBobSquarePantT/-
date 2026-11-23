"""软件包搜索与版本查询 API。"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import APIRouter, Query

from ..utils.system import current_platform, detect_package_manager

router = APIRouter(prefix="/api", tags=["search"])

# ---------------- 缓存（统一入口，避免重复定义） ----------------
_SEARCH_CACHE: dict = {}
_SEARCH_CACHE_TTL = 300      # 搜索结果缓存 5 分钟
_SEARCH_CACHE_MAX = 80       # 缓存条目上限，超出后整体清空
_MAX_RESULTS = 200           # 单次关键词最多保留的结果数（分页在此基础上切分）

_DESC_CACHE: dict = {}
_DESC_CACHE_TTL = 600        # 描述缓存 10 分钟


def _user_config_dir() -> str:
    """运行期数据（缓存等）存放目录，与代码/配置分离。"""
    configured = os.environ.get("DEVBOX_CONFIG_DIR")
    if configured:
        return configured
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DevBox")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "devbox")


_DESC_PERSIST_FILE = os.path.join(_user_config_dir(), "desc_cache.json")
_LAST_DESC_SAVE = 0.0


def _load_desc_cache() -> None:
    """启动时从磁盘加载描述缓存，避免重启后重复查询。"""
    global _DESC_CACHE
    try:
        if os.path.exists(_DESC_PERSIST_FILE):
            with open(_DESC_PERSIST_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            _DESC_CACHE = {
                k: (v, time.time())
                for k, v in raw.items()
                if isinstance(v, str) and v
            }
    except Exception:
        _DESC_CACHE = {}


def _save_desc_cache() -> None:
    """将描述缓存写入磁盘（限频，至少间隔 30 秒，避免写盘风暴）。"""
    global _LAST_DESC_SAVE
    now = time.time()
    if now - _LAST_DESC_SAVE < 30:
        return
    _LAST_DESC_SAVE = now
    try:
        data = {k: v[0] for k, v in _DESC_CACHE.items() if v and v[0]}
        os.makedirs(os.path.dirname(_DESC_PERSIST_FILE), exist_ok=True)
        with open(_DESC_PERSIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _search_cache_key(q: str, page: int, page_size: int) -> tuple:
    return (current_platform(), q.strip().lower(), page, page_size)


def _search_cache_get(key: tuple) -> dict | None:
    entry = _SEARCH_CACHE.get(key)
    if entry and time.time() - entry[1] < _SEARCH_CACHE_TTL:
        return entry[0]
    return None


def _search_cache_set(key: tuple, data) -> None:
    if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[key] = (data, time.time())


_load_desc_cache()


# ---------------- 基础命令 ----------------
def _run(cmd: List[str], timeout: int = 60) -> str:
    """同步执行命令并返回合并输出。"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout + result.stderr
    except Exception:
        return ""


def _parse_winget_json(text: str) -> List[dict]:
    """解析 winget --output json 输出（兼容多种字段结构）。"""
    try:
        start = text.find("{")
        if start == -1:
            return []
        data = json.loads(text[start:])
        rows = data.get("SearchResult") or data.get("searchResult") or []
        results = []
        for row in rows:
            match = row.get("Match") or row
            result = match.get("Result") or match
            results.append(
                {
                    "name": result.get("Name") or "",
                    "id": result.get("Id") or "",
                    "version": result.get("Version") or "",
                    "source": result.get("Source") or "",
                    "description": result.get("Description") or "",
                }
            )
        return results
    except Exception:
        return []


def _parse_winget_table(text: str) -> List[dict]:
    """解析 winget search 表格输出（尽力而为）。"""
    results = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if re.match(r"^\s*-{3,}\s*$", line):
            continue
        if re.match(r"^\s*(名称|Name)\s", line):
            continue
        # 数据行: 名称(可含空格)  ID  Version  Match  Source
        cols = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        if len(cols) >= 4 and re.match(r"^[\w.\-]+$", cols[1]) and cols[1] != "Tag:":
            version = cols[2].split("Tag:")[0].strip()
            source = cols[-1]
            if " " in source:
                source = source.split()[-1]
            if version:
                results.append(
                    {
                        "name": cols[0],
                        "id": cols[1],
                        "version": version,
                        "source": source,
                        "description": "",
                    }
                )
            continue
        # 兼容性回退：尝试更宽松的正则
        match = re.match(
            r"^(?P<name>.+?)\s{2,}(?P<id>\S+)\s+(?P<version>\S+)"
            r"(?:\s+(?P<match>\S+))?\s+(?P<source>\S+)\s*$",
            line,
        )
        if match and match.group("id") != "Tag:":
            version = match.group("version").split("Tag:")[0].strip()
            if version:
                results.append(
                    {
                        "name": match.group("name").strip(),
                        "id": match.group("id"),
                        "version": version,
                        "source": match.group("source"),
                        "description": "",
                    }
                )
    return results


def _winget_search(keyword: str) -> List[dict]:
    """调用 winget search 并解析结果，优先 JSON 输出，失败再回退表格输出。"""
    # 部分 winget 版本不支持 search --output json，探测超时设短，避免白白等待
    json_output = _run(
        [
            "winget", "search", keyword,
            "--source", "winget",
            "--accept-source-agreements", "--disable-interactivity",
            "--output", "json",
        ],
        timeout=15,
    )
    results = _parse_winget_json(json_output)
    if not results:
        # 表格搜索在部分网络环境下首次执行很慢（源索引更新），超时给足
        table_output = _run(
            [
                "winget", "search", keyword,
                "--accept-source-agreements", "--disable-interactivity",
            ],
            timeout=90,
        )
        results = _parse_winget_table(table_output)
    cleaned = []
    for item in results:
        rid = (item.get("id") or "").strip()
        version = (item.get("version") or "").strip()
        if not rid or " " in rid or rid == "Tag:":
            continue
        if item.get("source") == "msstore" and version.lower() in ("unknown", ""):
            continue
        cleaned.append(item)
    return cleaned


def _linux_search(keyword: str) -> List[dict]:
    """调用 Linux 包管理器搜索。"""
    manager = detect_package_manager() or ""
    if manager == "apt":
        output = _run(["apt-cache", "search", keyword])
        results = []
        for line in output.splitlines():
            if " - " in line:
                name, desc = line.split(" - ", 1)
                results.append(
                    {"name": name.strip(), "id": name.strip(), "version": "",
                     "source": "apt", "description": desc.strip()}
                )
        return results
    if manager == "pacman":
        output = _run(["pacman", "-Ss", keyword])
        results = []
        for line in output.splitlines():
            line = line.strip()
            if "/" in line:
                parts = line.split(None, 1)
                full = parts[0]
                name = full.split("/")[-1]
                version = parts[1] if len(parts) > 1 else ""
                results.append(
                    {"name": name, "id": name, "version": version,
                     "source": "pacman", "description": ""}
                )
        return results
    if manager in ("dnf", "yum"):
        output = _run([manager, "search", keyword])
        results = []
        for line in output.splitlines():
            line = line.strip()
            if " : " in line and not line.startswith("="):
                name, desc = line.split(" : ", 1)
                results.append(
                    {"name": name.strip(), "id": name.strip(), "version": "",
                     "source": manager, "description": desc.strip()}
                )
        return results
    return []


def _snap_search(keyword: str) -> List[dict]:
    """Linux: 如安装了 snap 则搜索。"""
    if not shutil.which("snap"):
        return []
    out = _run(["snap", "find", keyword])
    results = []
    for line in out.splitlines():
        cols = line.split()
        if len(cols) >= 3 and cols[0] != "Name":
            results.append(
                {"name": cols[0], "id": cols[0],
                 "version": cols[2] if len(cols) > 2 else "",
                 "source": "snap",
                 "description": " ".join(cols[3:]) if len(cols) > 3 else ""}
            )
    return results


def _choco_search(keyword: str) -> List[dict]:
    """如安装了 Chocolatey 则搜索。"""
    if not shutil.which("choco"):
        return []
    out = _run(["choco", "search", keyword, "--limit-output", "--limit", "20"])
    results = []
    for line in out.splitlines():
        if "|" in line:
            name, version = line.split("|", 1)
            results.append(
                {"name": name.strip(), "id": name.strip(), "version": version.strip(),
                 "source": "choco", "description": ""}
            )
    return results


def _scoop_search(keyword: str) -> List[dict]:
    """如安装了 Scoop 则搜索。"""
    if not shutil.which("scoop"):
        return []
    out = _run(["scoop", "search", keyword])
    results = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Name", "Results", "----", "WARN", "ERR", "Searching", "搜索")):
            continue
        cols = line.split()
        if cols:
            results.append(
                {"name": cols[0], "id": cols[0],
                 "version": cols[1] if len(cols) > 1 else "",
                 "source": "scoop", "description": ""}
            )
    return results


# ---------------- 描述补充 ----------------
def _fetch_description(package_id: str, source: str = "") -> str:
    """查询单个包的描述（带内存+磁盘缓存），指定 winget 源以加速。"""
    cached = _DESC_CACHE.get(package_id)
    if cached and time.time() - cached[1] < _DESC_CACHE_TTL:
        return cached[0]
    cmd = ["winget", "show", package_id, "--accept-source-agreements", "--disable-interactivity"]
    if source == "winget" or "." in package_id:
        cmd += ["--source", "winget"]
    out = _run(cmd, timeout=10)
    desc = ""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:描述|Description):\s*(.*)$", line.strip())
        if not m:
            continue
        inline = m.group(1).strip()
        if inline:
            desc = inline
            break
        parts = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and (nxt.startswith("  ") or nxt.startswith("\t")):
                parts.append(nxt.strip())
            elif not nxt.strip():
                continue
            else:
                break
        desc = " ".join(parts).strip()
        break
    if len(desc) > 200:
        desc = desc[:200].rstrip() + "..."
    if desc:
        _DESC_CACHE[package_id] = (desc, time.time())
        _save_desc_cache()
    return desc


def _fill_descriptions(results: List[dict]) -> None:
    """并发补充最前面 5 条结果的描述。"""
    if not results:
        return
    targets = [r for r in results[:5] if not r.get("official")]
    if not targets:
        return
    ids = [r["id"] for r in targets]
    sources = [r.get("source", "") for r in targets]
    with ThreadPoolExecutor(max_workers=3) as pool:
        descs = list(pool.map(_fetch_description, ids, sources))
    for item, desc in zip(targets, descs):
        if desc:
            item["description"] = desc
    _save_desc_cache()


# ---------------- 中文/别名映射 ----------------
KEYWORD_ALIASES = {
    "微信": ["wechat", "weixin"],
    "qq": ["qq", "tencent.qq"],
    "钉钉": ["dingtalk"],
    "企业微信": ["wecom"],
    "数据库": ["mysql", "oracle", "sqlserver", "postgresql", "database"],
    "数据库工具": ["navicat", "dbeaver", "mysql workbench"],
    "解压": ["7zip", "bandizip", "winrar"],
    "压缩": ["7zip", "bandizip", "winrar"],
    "浏览器": ["chrome", "firefox", "edge", "browser"],
    "编辑器": ["visual studio code", "idea", "pycharm", "sublime text"],
    "环境": ["jdk", "nodejs", "python"],
    "java": ["jdk", "java"],
    "输入法": ["sogou", "baidu input"],
    "远程": ["teamviewer", "sunlogin", "todesk"],
    "下载": ["internet download manager", "aria2"],
}

SEARCH_SUGGESTIONS = ["git", "jdk", "mysql", "vscode", "python", "微信", "浏览器", "数据库"]


def _expand_keywords(keyword: str) -> List[str]:
    """根据别名映射扩展搜索词（英文别名优先命中，原词兜底，减少无效搜索）。"""
    candidates = []
    for key, aliases in KEYWORD_ALIASES.items():
        if key in keyword:
            candidates.extend(aliases)
    candidates.append(keyword)
    seen = set()
    out = []
    for k in candidates:
        k = k.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ---------------- 官网下载库 ----------------
OFFICIAL_SITES = [
    {
        "name": "Oracle Database",
        "keywords": ["oracle", "oracle database", "oracle数据库"],
        "url": "https://www.oracle.com/database/",
        "description": "Oracle 数据库服务器：官方下载需注册 Oracle 账号",
    },
    {
        "name": "微信",
        "keywords": ["微信", "wechat", "weixin"],
        "url": "https://pc.weixin.qq.com/",
        "description": "微信 Windows 客户端官方下载",
    },
    {
        "name": "QQ",
        "keywords": ["qq", "腾讯qq"],
        "url": "https://im.qq.com/",
        "description": "QQ 官方下载",
    },
    {
        "name": "钉钉",
        "keywords": ["钉钉", "dingtalk"],
        "url": "https://www.dingtalk.com/",
        "description": "钉钉官方下载",
    },
    {
        "name": "企业微信",
        "keywords": ["企业微信", "wecom"],
        "url": "https://work.weixin.qq.com/",
        "description": "企业微信官方下载",
    },
    {
        "name": "向日葵远程控制",
        "keywords": ["向日葵", "sunlogin"],
        "url": "https://sunlogin.oray.com/",
        "description": "向日葵远程控制官方下载",
    },
    {
        "name": "TeamViewer",
        "keywords": ["teamviewer", "远程"],
        "url": "https://www.teamviewer.com/",
        "description": "TeamViewer 远程控制官方下载",
    },
    {
        "name": "MATLAB",
        "keywords": ["matlab"],
        "url": "https://www.mathworks.com/products/matlab.html",
        "description": "MATLAB 官方下载（需许可证）",
    },
    {
        "name": "Microsoft Office",
        "keywords": ["office", "office365", "word", "excel"],
        "url": "https://www.microsoft.com/microsoft-365",
        "description": "Microsoft 365 / Office 官方页面",
    },
    {
        "name": "WPS Office",
        "keywords": ["wps", "wps office"],
        "url": "https://www.wps.cn/",
        "description": "WPS Office 官方下载",
    },
    {
        "name": "淘宝虾店",
        "keywords": ["淘宝", "taobao"],
        "url": "https://www.taobao.com/",
        "description": "淘宝官网",
    },
]


def _official_matches(keyword: str) -> List[dict]:
    """匹配官网下载库。"""
    ql = keyword.strip().lower()
    hits = []
    for site in OFFICIAL_SITES:
        kws = [k.lower() for k in site["keywords"]]
        if any(k == ql or k in ql or ql in k for k in kws):
            hits.append(site)
    return hits


def _official_to_result(site: dict) -> dict:
    return {
        "name": site["name"],
        "id": "official:" + site["name"],
        "version": "",
        "source": "官网",
        "description": site["description"],
        "url": site["url"],
        "official": True,
    }


# ---------------- Windows 搜索与聚合 ----------------
def _search_windows(keyword: str) -> List[dict]:
    """多候选词搜索：并行执行前 3 个候选词，命中足够即停止，减少等待。"""
    keywords = _expand_keywords(keyword)[:3]
    results_by_kw = {}
    if len(keywords) > 1:
        with ThreadPoolExecutor(max_workers=len(keywords)) as pool:
            for kw, items in zip(keywords, pool.map(_winget_search, keywords)):
                results_by_kw[kw] = items
    else:
        results_by_kw[keywords[0]] = _winget_search(keywords[0])
    merged = []
    seen = set()
    for kw in keywords:
        for item in results_by_kw.get(kw, []):
            if item["id"] not in seen:
                seen.add(item["id"])
                merged.append(item)
        if len(merged) >= 5:
            break
    for item in _choco_search(keyword) + _scoop_search(keyword):
        if item["id"] not in seen:
            seen.add(item["id"])
            merged.append(item)
    return merged


def _rank_results(results: List[dict], keyword: str) -> List[dict]:
    """按相关度排序：精确匹配 > 前缀匹配 > 包含匹配 > 其他；winget 源优先。"""
    kw = keyword.lower()

    def score(item: dict) -> int:
        name = (item.get("name") or "").lower()
        rid = (item.get("id") or "").lower()
        name_clean = re.sub(r"[\s\-]+", "", name)
        if name == kw or name_clean == kw or rid == kw:
            return 0
        if name.startswith(kw) or rid.startswith(kw):
            return 1
        if kw in name or kw in rid:
            return 2
        return 3

    return sorted(
        results,
        key=lambda it: (score(it), it.get("source") != "winget", (it.get("name") or "").lower()),
    )


def _is_version_token(token: str) -> bool:
    """判断 token 是否为版本号（如 11、7.0.0.20、v1.0.77）。"""
    t = token.lower()
    if re.fullmatch(r"\d+(\.\d+)*", t):
        return True
    if re.fullmatch(r"v\d+(\.\d+)[\w.\-]*", t):
        return True
    return False


def _normalize_name(name: str) -> str:
    """移除名称中的版本号，用于同类软件合并。"""
    toks = [
        t for t in re.split(r"\s+", (name or "").strip().lower())
        if t and not _is_version_token(t)
    ]
    return " ".join(toks)


def _aggregate_results(results: List[dict]) -> List[dict]:
    """同类软件（名称去版本号后相同）合并为一条，多个来源/版本记录到 variants。"""
    groups = {}
    for item in results:
        key = _normalize_name(item.get("name"))
        if not key:
            continue
        groups.setdefault(key, []).append(item)

    merged = []
    for name, items in groups.items():
        main = items[0]
        variants = []
        seen = set()
        for it in items:
            version = it.get("version") or ""
            source = it.get("source") or ""
            tag = f"{version}@{source}"
            if version and version.lower() != "unknown" and tag not in seen:
                seen.add(tag)
                variants.append(
                    {"version": version, "source": source, "id": it.get("id") or ""}
                )
        if variants:
            main["variants"] = variants
        merged.append(main)
    return merged


def _cached_aggregated_search(keyword: str) -> List[dict]:
    """带缓存的关键词搜索 + 聚合 + 排序。"""
    cache_key = (current_platform(), keyword.lower().strip())
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and time.time() - cached[1] < _SEARCH_CACHE_TTL:
        return cached[0]
    if current_platform() == "windows":
        results = _aggregate_results(_rank_results(_search_windows(keyword), keyword))[:_MAX_RESULTS]
    else:
        results = _aggregate_results(_linux_search(keyword) + _snap_search(keyword))[:_MAX_RESULTS]
    # 空结果多半是命令超时/网络问题，不缓存，避免一段时间内都搜不到东西
    if results:
        _search_cache_set(cache_key, results)
    return results


# ---------------- API ----------------
def _build_search_response(q: str, page: int, page_size: int) -> dict:
    """构建一页搜索结果（含官网补充与描述填充）。"""
    results = list(_cached_aggregated_search(q))
    # 官网条目合并进完整列表后再统一分页，保证 total 与实际页数一致、且不会重复出现。
    existing_names = {r["name"].strip().lower() for r in results}
    existing_ids = {r.get("id") for r in results}
    for site in reversed(_official_matches(q)):
        item = _official_to_result(site)
        name_key = item["name"].strip().lower()
        if name_key in existing_names or item["id"] in existing_ids:
            continue
        results.insert(0, item)
        existing_names.add(name_key)
        existing_ids.add(item["id"])
    total = len(results)
    start = (page - 1) * page_size
    page_results = results[start:start + page_size]
    if current_platform() == "windows":
        _fill_descriptions(page_results)
    return {
        "platform": current_platform(),
        "keyword": q,
        "total": total,
        "page": page,
        "page_size": page_size,
        "results": page_results,
        "suggestions": SEARCH_SUGGESTIONS if not results else [],
    }


@router.get("/search")
async def search_packages(
    q: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """搜索可安装的软件包（中文别名、官网下载、分页、结果缓存）。"""
    cache_key = _search_cache_key(q, page, page_size)
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached
    resp = await asyncio.to_thread(_build_search_response, q, page, page_size)
    # 空结果通常是上游命令超时，不缓存，下一次搜索重新尝试
    if resp["total"]:
        _search_cache_set(cache_key, resp)
    return resp


@router.get("/versions")
async def package_versions(package: str = Query(...)):
    """查询软件包可用版本。"""
    if current_platform() == "windows":
        output = _run(
            [
                "winget", "show", package, "--versions",
                "--accept-source-agreements", "--disable-interactivity",
            ],
            timeout=45,
        )
        versions = []
        for line in output.splitlines():
            line = line.strip()
            if line and re.match(r"^[\d.]+", line):
                versions.append(line)
        return {"package": package, "versions": versions}
    return {"package": package, "versions": []}