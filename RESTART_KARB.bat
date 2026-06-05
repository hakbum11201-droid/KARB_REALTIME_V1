@echo off
setlocal
cd /d C:\KARB_REALTIME_V1

echo ============================================
echo   KARB_REALTIME_V1 - RESTART
echo ============================================

call STOP_KARB.bat /nopause
timeout /t 2 /nobreak >nul
call START_KARB.bat
endlocal
