@echo off
echo =======================================================
echo   DEPRECATED: Please use the STOP button in the UI.
echo   Use this only in case of emergency.
echo =======================================================
echo.
cd /d C:\KARB_REALTIME_V1
python src\control.py stop
echo.
echo   Engine will finalize session report on next loop.
pause
