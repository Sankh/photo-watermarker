# 照片批量水印工具 · Windows 打包脚本
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
# 产物：dist\PhotoWatermark\（目录版，启动快）与 dist\PhotoWatermark.exe（单文件版）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = "python"
$common = @(
    "--noconfirm", "--windowed",
    "--icon", "assets/icon.ico",
    "--add-data", "assets/icon.ico;.",
    "--add-data", "assets/icon-64.png;.",
    "--add-data", "assets/fonts;assets/fonts",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc",
    "--exclude-module", "setuptools",
    "src/photo_watermark.py"
)

Write-Host "==> 打包目录版（推荐）" -ForegroundColor Cyan
& $py -m PyInstaller @common --onedir --name PhotoWatermark --distpath dist --workpath build/dir --specpath build

Write-Host "==> 打包单文件版" -ForegroundColor Cyan
& $py -m PyInstaller @common --onefile --name PhotoWatermark --distpath dist --workpath build/onefile --specpath build

Write-Host ""
Write-Host "完成：dist\PhotoWatermark\PhotoWatermark.exe（目录版，约 2.5s 启动）" -ForegroundColor Green
Write-Host "      dist\PhotoWatermark.exe（单文件版，首次启动需解包，较慢）" -ForegroundColor Green