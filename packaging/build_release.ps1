# Build the Windows release: PyInstaller bundle, a --selftest smoke check, and a zip.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1
#         powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1 -App legacy
#
#   -App studio   (default) Manga Studio, the PySide6 workbench
#   -App legacy   the older floating tool (customtkinter)
#
# Output: dist\<name>\             the runnable folder
#         dist\<name>-win64.zip    release asset

param(
    [ValidateSet("studio", "legacy")]
    [string]$App = "studio",
    [switch]$SkipSelfTest
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($App -eq "studio") {
    $spec = "manga_studio.spec"; $name = "MangaStudio"; $readme = "README_studio.txt"
} else {
    $spec = "manga_ocr_translate.spec"; $name = "MangaOcrTranslate"; $readme = "README.txt"
}

$python = Join-Path $root ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $root ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python（请先按 README 创建 .venv 并安装依赖）"
}
if (-not (Test-Path $pyinstaller)) {
    Write-Host "未安装 PyInstaller，正在安装…"
    & $python -m pip install -r "requirements-build.txt"
}
if (-not (Test-Path (Join-Path $root "models\comic-text-detector.onnx"))) {
    Write-Host "检测模型缺失，先下载…"
    & $python "scripts\download_models.py"
}

Write-Host "== PyInstaller 打包（$spec）=="
& $pyinstaller --noconfirm --clean $spec

$dist = Join-Path $root "dist\$name"
if (-not (Test-Path $dist)) {
    throw "打包失败：未生成 $dist"
}

if ($App -eq "studio" -and -not $SkipSelfTest) {
    # Start the built program, wait for the models to load, read its verdict.
    Write-Host "== 冒烟检查（--selftest，首次约需 20 秒）=="
    $result = Join-Path $root "dist\selftest-result.txt"
    Remove-Item $result -ErrorAction SilentlyContinue
    $env:QT_QPA_PLATFORM = "offscreen"
    $process = Start-Process -FilePath (Join-Path $dist "$name.exe") `
        -ArgumentList "--selftest", "`"$result`"" -PassThru -Wait
    Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
    $verdict = if (Test-Path $result) { (Get-Content $result -Raw).Trim() } else { "(没有生成结果文件)" }
    Write-Host "自检结果：$verdict（退出码 $($process.ExitCode)）"
    if ($process.ExitCode -ne 0 -or -not $verdict.StartsWith("OK")) {
        throw "冒烟检查失败，不生成发布包。"
    }
    Remove-Item $result -ErrorAction SilentlyContinue
}

Remove-Item (Join-Path $dist "manga_extract.log") -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root "packaging\$readme") (Join-Path $dist "README.txt") -Force

$zip = Join-Path $root "dist\$name-win64.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue

Write-Host "== 压缩（约 1 GB，需要几分钟） =="
Push-Location (Join-Path $root "dist")
try {
    # Full path: a GNU tar earlier on PATH (Git Bash) would read "E:" as a remote host.
    & (Join-Path $env:SystemRoot "System32\tar.exe") -a -c -f $zip $name
} finally {
    Pop-Location
}

$mb = [math]::Round((Get-Item $zip).Length / 1MB)
Write-Host "完成：$zip（$mb MB）"
