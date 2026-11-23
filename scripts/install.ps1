# DevBox 一键启动脚本 (Windows PowerShell)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "[DevBox] 正在安装 Python 依赖..." -ForegroundColor Cyan
python -m pip install -r backend/requirements.txt

Write-Host "[DevBox] 正在启动 Web 服务: http://localhost:8080" -ForegroundColor Green
python run.py
