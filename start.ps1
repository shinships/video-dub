$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$EnvFile = Join-Path $Root ".env"
$FfmpegDir = Join-Path $Root "tools\ffmpeg"
$SharedFfmpegExe = Get-ChildItem `
  "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe" `
  -Recurse `
  -Filter ffmpeg.exe `
  -ErrorAction SilentlyContinue |
  Select-Object -First 1

if (-not (Test-Path $Python)) {
  throw "Missing .venv or Python dependencies."
}

if (-not (Test-Path $EnvFile)) {
  throw "Missing .env file."
}

[Environment]::SetEnvironmentVariable("GOOGLE_APPLICATION_CREDENTIALS", $null, "Process")

Get-Content $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith("#")) {
    $name, $value = $line -split "=", 2
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
  }
}

if ($SharedFfmpegExe) {
  $env:Path = "$($SharedFfmpegExe.Directory.FullName);$env:Path"
} elseif (Test-Path $FfmpegDir) {
  $env:Path = "$FfmpegDir;$env:Path"
}

Start-Process -FilePath $Python `
  -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8010" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden

Start-Process -FilePath "npm.cmd" `
  -ArgumentList "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173" `
  -WorkingDirectory (Join-Path $Root "frontend") `
  -WindowStyle Hidden

Start-Sleep -Seconds 3
Write-Host "Video Dub AI is running:" -ForegroundColor Green
Write-Host "  UI:     http://127.0.0.1:5173"
Write-Host "  API:    http://127.0.0.1:8010/docs"
Write-Host "  Health: http://127.0.0.1:8010/api/health"
