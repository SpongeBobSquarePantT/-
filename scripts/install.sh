#!/usr/bin/env bash
# DevBox 一键启动脚本 (Linux)
set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "[DevBox] 正在安装 Python 依赖..."
python3 -m pip install -r backend/requirements.txt

echo "[DevBox] 正在启动 Web 服务: http://localhost:8080"
python3 run.py
