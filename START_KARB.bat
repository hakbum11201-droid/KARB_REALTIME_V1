@echo off
setlocal
cd /d C:\KARB_REALTIME_V1

echo ============================================
echo   KARB_REALTIME_V1 - START
echo ============================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%%'\" | Where-Object { $_.CommandLine -like '*KARB_REALTIME_V1*' -and ($_.CommandLine -like '*app_launcher.py*' -or $_.CommandLine -like '*src\web_server.py*') }; if ($p) { Write-Host '[START] Existing KARB server/launcher appears to be running:'; $p | ForEach-Object { Write-Host ('  PID {0}: {1}' -f $_.ProcessId,$_.CommandLine) }; exit 2 }"
if %ERRORLEVEL% EQU 2 (
  echo [START] Open http://127.0.0.1:8000 if the dashboard is already running.
  start http://127.0.0.1:8000
  pause
  exit /b 0
)

echo [START] Launching app_launcher.py ...
start http://127.0.0.1:8000
python app_launcher.py
endlocal
