# 照片批量水印工具 · Windows 打包脚本
# 用法：powershell -ExecutionPolicy Bypass -File build.ps1
#       powershell -ExecutionPolicy Bypass -File build.ps1 -Python "C:\Python312\python.exe"
# 产物：dist\PhotoWatermark\PhotoWatermark.exe（目录版，启动快）
#       dist\PhotoWatermark.exe（单文件版，首次启动需解包）
param([string]$Python = "python")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = $Python
# 资源一律用绝对路径：--specpath 会把相对路径解析到 spec 所在目录，CI 上会因此找不到图标
$icon = Join-Path $root "assets\icon.ico"
$iconPng = Join-Path $root "assets\icon-64.png"
$fonts = Join-Path $root "assets\fonts"
$entry = Join-Path $root "src\photo_watermark.py"

$common = @(
    "--noconfirm", "--windowed",
    "--icon", $icon,
    "--add-data", "$icon;.",
    "--add-data", "$iconPng;.",
    "--add-data", "$fonts;assets/fonts",
    "--exclude-module", "numpy",
    "--exclude-module", "pandas",
    "--exclude-module", "matplotlib",
    "--exclude-module", "scipy",
    "--exclude-module", "unittest",
    "--exclude-module", "pydoc",
    "--exclude-module", "setuptools",
    $entry
)

function Invoke-Build([string[]]$extra, [string]$work) {
    & $py -m PyInstaller @common @extra --distpath dist --workpath "build\$work" --specpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($work), exit code $LASTEXITCODE" }
}

Write-Host "==> 打包目录版（推荐）" -ForegroundColor Cyan
Invoke-Build @("--onedir", "--name", "PhotoWatermark") "dir"

Write-Host "==> 打包单文件版" -ForegroundColor Cyan
Invoke-Build @("--onefile", "--name", "PhotoWatermark") "onefile"

Write-Host ""
Write-Host "完成：dist\PhotoWatermark\PhotoWatermark.exe（目录版，约 3s 启动）" -ForegroundColor Green
Write-Host "      dist\PhotoWatermark.exe（单文件版，首次启动需解包，较慢）" -ForegroundColor Green
