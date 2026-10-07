# Build the Windows release: PyInstaller bundle + zip ready for a GitHub release.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build_release.ps1
#
# Output: dist\MangaOcrTranslate\          (the runnable folder)
#         dist\MangaOcrTranslate-win64.zip (release asset)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $root ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python（请先按 README 创建 .venv 并安装依赖）"
}
if (-not (Test-Path (Join-Path $root "models\comic-text-detector.onnx"))) {
    Write-Host "检测模型缺失，先下载…"
    & $python "scripts\download_models.py"
}

Write-Host "== PyInstaller 打包 =="
& $pyinstaller --noconfirm --clean "manga_ocr_translate.spec"

$dist = Join-Path $root "dist\MangaOcrTranslate"
if (-not (Test-Path $dist)) {
    throw "打包失败：未生成 $dist"
}

Remove-Item (Join-Path $dist "manga_extract.log") -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root "packaging\README.txt") (Join-Path $dist "README.txt") -Force

$zip = Join-Path $root "dist\MangaOcrTranslate-win64.zip"
Remove-Item $zip -Force -ErrorAction SilentlyContinue

Write-Host "== 压缩（约 1 GB，需要几分钟） =="
Push-Location (Join-Path $root "dist")
try {
    tar.exe -a -c -f $zip "MangaOcrTranslate"
} finally {
    Pop-Location
}

$mb = [math]::Round((Get-Item $zip).Length / 1MB)
Write-Host "完成：$zip（$mb MB）"
