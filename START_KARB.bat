@echo off
setlocal
cd /d C:\KARB_REALTIME_V1

echo ============================================
echo   KARB_REALTIME_V1 - START
echo ============================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*KARB_REALTIME_V1*src\web_server.py*' -or $_.CommandLine -like '*KARB_REALTIME_V1*app_launcher.py*' }; if ($p) { Write-Host '[START] Existing KARB server/launcher appears to be running:'; $p | ForEach-Object { Write-Host ('  PID {0}: {1}' -f $_.ProcessId,$_.CommandLine) }; exit 2 }"
if %ERRORLEVEL% EQU 2 (
  echo [START] Open http://localhost:8000 if the dashboard is already running.
  pause
  exit /b 0
)

echo [START] Launching app_launcher.py ...
python app_launcher.py
endlocal
