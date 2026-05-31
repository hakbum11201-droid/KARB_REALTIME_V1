@echo off
title Build KARB_REALTIME_V1
cd /d C:\KARB_REALTIME_V1
echo ============================================
echo   Building KARB_REALTIME_V1 with PyInstaller
echo ============================================
echo.

pip install pyinstaller

pyinstaller --noconfirm ^
  --name KARB_REALTIME ^
  --add-data "web;web" ^
  --add-data "config;config" ^
  --add-data "docs;docs" ^
  app_launcher.py

echo.
echo Build complete! Check dist\KARB_REALTIME\KARB_REALTIME.exe
pause
