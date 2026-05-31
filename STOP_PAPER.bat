@echo off
cd /d C:\KARB_REALTIME_V1
echo ============================================
echo   KARB_REALTIME_V1 - Stop Requested
echo ============================================
echo.
python src\control.py stop
echo.
echo   Engine will finalize session report on next loop.
echo   Do NOT close the engine window manually.
echo.
pause
