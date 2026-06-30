$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Không tìm thấy Python trong PATH."
}

python -m venv (Join-Path $Root ".venv")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "backend\requirements-core.txt")

Push-Location (Join-Path $Root "frontend")
try {
  pnpm install
} finally {
  Pop-Location
}

Write-Host "Setup core xong. App chạy được ở demo mode." -ForegroundColor Green
Write-Host "Muốn pipeline thật, cài thêm:"
Write-Host "  .\.venv\Scripts\python -m pip install -r backend\requirements-cloud.txt"
Write-Host "  .\.venv\Scripts\python -m pip install -r backend\requirements-audio.txt"
Write-Host "Và cài FFmpeg + Google Cloud ADC theo README.md."
