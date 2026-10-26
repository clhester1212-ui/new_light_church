@echo off
rem Launcher for the Slack DM helper (same Python as nlc.cmd).
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u "%~dp0slack_dm.py" %*
