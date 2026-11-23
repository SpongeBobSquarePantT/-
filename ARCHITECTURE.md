# DevBox — 跨平台开发环境图形化安装工具

> 一个带 Web 界面的开发环境安装器：勾选想要安装的工具（JDK、Maven、Node.js、数据库、IDE 等），一键自动安装。
> 同时支持 **Windows**（winget）与 **Linux**（apt/dnf/yum/pacman），可扩展直接下载安装。

---

## 1. 项目目标

- **Web UI 勾选安装**：浏览器打开页面，按分类勾选工具，点击「安装」自动执行安装命令
- **跨平台支持**：同一套工具定义，分别在 Windows / Linux 上走各自的包管理器
- **实时进度**：安装日志通过 WebSocket 实时推送到页面
- **本地优先**：工具运行在本机，数据（工具清单、安装状态）存本地，无外部依赖服务

---

## 2. 技术选型

| 层次 | 选型 | 说明 |
|------|------|------|
| 后端框架 | Python + FastAPI | 轻量、自带 WebSocket、异步支持好 |
| 前端 | 原生 HTML + CSS + JS | 单页应用，无框架依赖，打开即用 |
| 安装引擎 | subprocess 调用包管理器 | Windows 用 winget，Linux 自动探测 apt/dnf/yum/pacman |
| 配置 | JSON 文件 | 工具清单集中定义，便于增删改 |
| 实时日志 | WebSocket | FastAPI 原生支持 |

---

## 3. 目录结构

```text
devbox/
├── run.py                            # 启动入口（--port / --stop / --force）
├── devbox.spec                       # PyInstaller 打包配置
├── backend/
│   ├── main.py                       # FastAPI 入口，注册路由、静态资源、lifespan
│   ├── requirements.txt
│   ├── api/
│   │   ├── tools.py                  # GET /api/tools（含安装状态检测）
│   │   ├── search.py                 # 搜索 / 版本 / 官网兜底 / 描述缓存
│   │   └── install.py                # 安装任务、状态、WS 日志、自定义安装
│   ├── installers/
│   │   ├── base.py                   # 安装器基类 + 子进程输出解码
│   │   ├── factory.py                # 按平台创建安装器
│   │   ├── windows.py                # Windows 安装引擎（winget + 探测）
│   │   └── linux.py                  # Linux 安装引擎（自动探测包管理器）
│   ├── config/
│   │   ├── loader.py                 # 配置读取与校验
│   │   └── tools.json                # 工具清单定义（核心配置）
│   ├── models/
│   │   └── tool.py                   # 请求数据模型
│   └── utils/
│       ├── system.py                 # 平台检测、包管理器探测
│       └── paths.py                  # 资源/数据目录（兼容打包运行）
├── frontend/
│   ├── index.html                    # 主页面
│   ├── css/style.css                 # 样式
│   └── js/app.js                     # 前端逻辑（渲染清单、勾选、安装、日志、搜索）
├── scripts/
│   ├── install.ps1                   # Windows 一键启动脚本
│   ├── install.sh                    # Linux 一键启动脚本
│   └── build.ps1                     # Windows 打包脚本
├── tests/
│   └── test_core.py                  # 核心逻辑单元测试
├── README.md                         # 使用说明
└── ARCHITECTURE.md                   # 本文档
```

---

## 4. 核心设计

### 4.1 工具清单（tools.json）

工具定义与平台解耦，每个工具同时声明 Windows / Linux 的安装方式：

```json
{
  "categories": [
    { "id": "java", "name": "Java", "icon": "☕" },
    { "id": "python", "name": "Python", "icon": "🐍" },
    { "id": "node", "name": "Node.js", "icon": "🟢" },
    { "id": "db", "name": "数据库", "icon": "🗄️" },
    { "id": "ide", "name": "IDE / 编辑器", "icon": "💻" },
    { "id": "tool", "name": "常用工具", "icon": "🔧" }
  ],
  "tools": [
    {
      "id": "jdk17",
      "name": "OpenJDK 17 (Temurin)",
      "category": "java",
      "description": "Java 17 LTS 运行时与编译工具",
      "windows": { "type": "winget", "package": "EclipseAdoptium.Temurin.17.JDK" },
      "linux": {
        "apt": "openjdk-17-jdk",
        "dnf": "java-17-openjdk-devel",
        "pacman": "jdk17-openjdk"
      },
      "check": "java -version 2>&1"
    }
  ]
}
```

### 4.2 安装引擎（多态）

```text
InstallerBase (抽象基类)
├── WindowsInstaller   -> 调用 winget install
└── LinuxInstaller     -> 探测包管理器后调用 apt-get/dnf/yum/pacman
```

- 后端启动时根据 `platform.system()` 选择对应安装器
- Linux 上通过 `shutil.which` 探测可用的包管理器，按优先级使用
- 每个工具可配置 `check` 命令，用于判断是否已安装

### 4.3 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tools` | 返回分类 + 工具清单（含已安装状态）；`?refresh=1` 触发后台重新检测 |
| POST | `/api/install` | 接收工具 id 列表，开始安装任务 |
| GET | `/api/status/{task_id}` | 查询任务状态与历史日志 |
| DELETE | `/api/status/{task_id}` | 取消任务（排队中立即取消；自定义包安装返回 409） |
| WS | `/api/ws/logs?task_id=` | 实时推送安装日志，连接时补发历史 |
| GET | `/api/search?q=&page=&page_size=` | 搜索可安装软件包（winget / apt / pacman / dnf） |
| GET | `/api/versions?package=` | 查询指定软件包的可用版本 |
| POST | `/api/install/package` | 自定义安装（包 ID + 可选版本 / 路径 / 来源） |
| GET | `/` | 前端页面 |

### 4.4 前端交互流程

```text
打开页面
  -> GET /api/tools 渲染分类与工具卡片
  -> 勾选工具 -> 点击「安装选中」
  -> POST /api/install 创建任务
  -> 建立 WS /api/ws/logs 订阅实时日志
  -> 逐条显示：开始安装 / 输出 / 完成 / 失败
  -> 任务结束刷新已安装状态
```

---

## 5. 安装流程（引擎内部）

```text
用户点击安装
  -> 后端校验工具 id 与平台可用性
  -> 逐工具执行：
       1. 执行 check 命令，已安装则跳过
       2. 组装包管理器命令（Windows: winget; Linux: 探测到的管理器）
       3. subprocess 异步执行，实时捕获 stdout/stderr
       4. 日志经 WebSocket 推送到前端
  -> 全部完成后汇总：成功 / 跳过 / 失败
```

---

## 6. 依赖

```text
fastapi
uvicorn[standard]
```

前端无第三方依赖。

---

## 7. 启动方式

### Windows

```powershell
cd devbox
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

或运行 `scripts/install.ps1`。

### Linux

```bash
cd devbox
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8080
```

或运行 `scripts/install.sh`。

浏览器打开 `http://localhost:8080`。

---

## 8. 后续可扩展

- **环境变量配置**：安装 JDK 后自动设置 JAVA_HOME / PATH
- **下载安装兜底**：winget / 包管理器没有时，走官方 URL 直接下载
- **安装后校验**：执行 `java -version`、`node -v` 等验证并展示
- **配置导出/导入**：把勾选清单导出为 JSON，换机一键导入
- **Docker 环境**：生成 Dockerfile / devcontainer 配置
