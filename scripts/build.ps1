# DevBox 打包脚本 (Windows)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host "[DevBox] 正在打包..." -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean devbox.spec

Write-Host "[DevBox] 构建完成: dist\DevBox.exe" -ForegroundColor Green
Write-Host "[DevBox] 运行后数据目录为 exe 同级的 downloads\（可用 DEVBOX_DOWNLOAD_DIR 覆盖）"
