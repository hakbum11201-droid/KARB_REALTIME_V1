@echo off
title KARB Dashboard
cd /d C:\KARB_REALTIME_V1
echo ============================================
echo   KARB_REALTIME_V1 - Dashboard UI
echo ============================================
echo.
echo   Open http://localhost:8000 in your browser
echo.
python src\web_server.py --port 8000
