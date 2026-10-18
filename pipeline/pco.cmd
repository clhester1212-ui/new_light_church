@echo off
rem Launcher for the Planning Center notes tool (same Python as nlc.cmd).
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u "%~dp0pco.py" %*
