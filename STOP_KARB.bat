@echo off
setlocal
cd /d C:\KARB_REALTIME_V1

echo ============================================
echo   KARB_REALTIME_V1 - STOP
echo ============================================

powershell -NoProfile -ExecutionPolicy Bypass -Command "$root='C:\KARB_REALTIME_V1'; $targets=Get-CimInstance Win32_Process -Filter \"Name LIKE 'python%%'\" | Where-Object { $_.CommandLine -like '*KARB_REALTIME_V1*' -and ($_.CommandLine -like '*app_launcher.py*' -or $_.CommandLine -like '*src\web_server.py*' -or $_.CommandLine -like '*src\main.py*') }; if (-not $targets) { Write-Host '[STOP] No KARB python process found.'; exit 0 }; foreach ($p in $targets) { Write-Host ('[STOP] Terminating PID {0}: {1}' -f $p.ProcessId,$p.CommandLine); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; exit 0"

echo [STOP] Done.
if /I "%~1" NEQ "/nopause" pause
endlocal
