# DevBox — 跨平台开发环境图形化安装工具

带 Web 界面的开发环境安装器：勾选想要安装的工具（JDK、Maven、Node.js、数据库、IDE 等），一键自动安装。

## 特性

- Web UI 勾选安装，实时日志推送（WebSocket，连接时补发历史日志）
- Windows 走 winget，Linux 自动探测 apt / dnf / yum / pacman / snap
- 工具清单集中在 `backend/config/tools.json`，启动时校验（重复 id / 未知分类 / 缺字段会明确报错）
- 已安装状态自动检测（多级策略：版本探测 / PATH / 注册表 / winget / 安装路径 / 命令）
- 「搜索安装」标签：搜索任意软件包（winget / apt / pacman / dnf），可指定版本与安装路径
- 安装任务串行执行、支持取消；保留最近 20 个任务，单任务日志上限 2000 行
- 可打包为单文件 exe（PyInstaller）

## 快速开始

### 方式一：一键启动脚本

```powershell
# Windows（在任意目录执行）
powershell -ExecutionPolicy Bypass -File F:\todo\devbox\scripts\install.ps1
```

```bash
# Linux
chmod +x scripts/install.sh
./scripts/install.sh
```

### 方式二：直接启动

```bash
cd F:\todo\devbox            # 先进入项目目录
pip install -r backend/requirements.txt
python run.py                # 或: python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

> 需要 Python 3.10+（开发环境为 3.13）。
> 注意：直接使用 `uvicorn` 时必须先在 `devbox` 目录下执行，否则会报 `ModuleNotFoundError: No module named 'backend'`。用 `run.py` 则可以在任意目录启动。

浏览器打开 `http://localhost:8080`。

## 测试

```bash
python -m unittest discover -s tests -t .
```

## 打包为 exe

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

产物为 `dist\DevBox.exe`（单文件，16MB 左右，首次启动需解包 2-3 秒）。**目标机器不需要安装 Python 或 pip**，双击 exe 即可运行，关闭控制台窗口即停止服务。打包后 `downloads\` 位于 exe 同级目录，可用 `DEVBOX_DOWNLOAD_DIR` 覆盖；描述缓存位于 `%APPDATA%\DevBox\`（可用 `DEVBOX_CONFIG_DIR` 覆盖）。

## 端口管理

```powershell
# 使用自定义端口
python run.py --port 9090

# 停止服务并释放端口（默认只停止确认属于 DevBox 的进程）
python run.py --stop
python run.py --stop --port 9090
python run.py --stop --force      # 不校验进程归属，强制停止占用者

# 启动前自动检查端口占用，被占用会给出提示
```

## API

任务状态依次为 `queued` → `running` → `done`；日志消息带 `level`，任务结束的消息带 `event: "done"`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tools` | 分类 + 工具清单（含已安装状态）；`?refresh=1` 触发后台重新检测 |
| POST | `/api/install` | 提交 `{"tool_ids": [...]}` 开始安装 |
| GET | `/api/status/{task_id}` | 查询任务状态与日志 |
| DELETE | `/api/status/{task_id}` | 取消任务：排队中立即取消，运行中在当前工具结束后停止，自定义包安装返回 409 |
| WS | `/api/ws/logs?task_id=...` | 实时推送日志；连接时补发该任务历史日志 |
| GET | `/api/search?q=关键词` | 搜索可安装软件包（平台自动切换 winget / 系统包管理器） |
| GET | `/api/versions?package=ID` | 查询指定软件包的可用版本列表 |
| POST | `/api/install/package` | 自定义安装：`{"package_id","name","version","install_path","source"}` |

## 目录结构

```text
devbox/
├── run.py                    # 启动入口（--port / --stop / --force）
├── devbox.spec               # PyInstaller 打包配置
├── backend/
│   ├── main.py               # FastAPI 入口（lifespan 启动检测）
│   ├── installers/           # 跨平台安装引擎
│   │   ├── base.py           #   基类 + 子进程输出解码
│   │   ├── factory.py        #   按平台创建安装器
│   │   ├── windows.py        #   PATH/注册表/winget/版本探测
│   │   └── linux.py          #   apt/dnf/yum/pacman 实现
│   ├── api/                  # 工具清单 / 安装 / 搜索 / 日志 API
│   ├── models/               # 数据模型
│   ├── config/tools.json     # 工具清单定义（启动时校验）
│   └── utils/                # 平台检测、资源与数据路径
├── frontend/                 # 原生 HTML/CSS/JS 单页
├── scripts/                  # 启动与打包脚本
├── tests/                    # 单元测试
├── ARCHITECTURE.md           # 架构设计文档
└── .gitignore
```

## 新增工具

编辑 `backend/config/tools.json`，在 `tools` 数组追加一条：

```json
{
  "id": "my-tool",
  "name": "My Tool",
  "category": "tool",
  "description": "工具说明",
  "windows": {
    "type": "winget",
    "package": "Publisher.Package",
    "executables": ["mytool"],
    "check": "mytool --version"
  },
  "linux": { "apt": "mytool", "dnf": "mytool", "pacman": "mytool", "check": "mytool --version" }
}
```

字段说明：
- `executables`：可执行文件名列表，用于 PATH 探测（绿色安装/自定义目录最可靠）
- `registry`：Windows 注册表已安装软件关键词
- `paths`：常见安装路径（支持 `*` 通配符与环境变量）
- `check`：命令检测兜底
- `java_version`：JDK 工具专用，精确匹配版本号

配置在启动时校验：`id` 不可重复、`category` 必须存在、`id/name/category/description` 必填、`windows` 和 `linux` 至少定义一个，出错会直接启动失败并指出具体工具。

## 注意事项

- 安装任务串行执行：同一时间只运行一个任务，其余排队；可在日志面板点「取消任务」
- Linux 安装需要 sudo 权限（安装命令通过 `sudo` 执行），首次使用会提示输入密码
- Windows 需系统自带 winget（Win10/11 默认安装，或 Microsoft Store 安装 App Installer）
- 各发行版包名可能略有差异，工具清单可自行调整
- 若启动报 `10048` 端口占用，执行 `python run.py --stop` 释放端口后重试
- 服务默认监听 `0.0.0.0` 且没有鉴权，局域网内任何人都能触发安装；仅在可信网络使用
- 「搜索安装」会改写 winget 全局 `settings.json` 的下载目录，以便保留安装包
- 运行数据：`downloads\`（项目内或 exe 同级，`DEVBOX_DOWNLOAD_DIR` 覆盖）、描述缓存 `%APPDATA%\DevBox\`（`DEVBOX_CONFIG_DIR` 覆盖）