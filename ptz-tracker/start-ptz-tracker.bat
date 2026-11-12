@echo off
title New Light PTZ Tracker
cd /d "%~dp0"
start "" http://localhost:8890
python run.py
pause
