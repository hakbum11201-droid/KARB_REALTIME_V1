@echo off
title KARB Paper Engine
cd /d C:\KARB_REALTIME_V1
echo ============================================
echo   KARB_REALTIME_V1 - Paper Engine Start
echo ============================================
echo.
echo   Mode: paper (--until-stop)
echo   Stop: run STOP_PAPER.bat or UI STOP button
echo.
python src\main.py --until-stop
echo.
echo   Engine stopped. Check reports\sessions\ for session report.
pause
