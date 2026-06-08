@echo off
setlocal
cd /d C:\KARB_REALTIME_V1

echo ============================================
echo   KARB_REALTIME_V1 - START
echo ============================================

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%%'\" | Where-Object { $_.CommandLine -like '*KARB_REALTIME_V1*' -and ($_.CommandLine -like '*app_launcher.py*' -or $_.CommandLine -like '*src\web_server.py*') }; if ($p) { Write-Host '[START] Existing KARB server/launcher appears to be running:'; $p | ForEach-Object { Write-Host ('  PID {0}: {1}' -f $_.ProcessId,$_.CommandLine) }; exit 2 }"
if %ERRORLEVEL% EQU 2 (
  echo [START] KARB server is already running. Please check your existing browser tab or open http://127.0.0.1:8000 manually.
  pause
  exit /b 0
)

echo [START] Launching app_launcher.py ...
if /I "%~1" EQU "/nobrowser" (
  python app_launcher.py --no-browser
) else (
  python app_launcher.py
)
endlocal
