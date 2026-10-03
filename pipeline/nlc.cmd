@echo off
rem Launcher that always finds the real Python + ffmpeg (Windows has a fake "python" Store alias).
rem Usage:  pipeline\nlc.cmd <command> [--date YYYY-MM-DD] ...   (see pipeline\nlc.py)
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*") do for /d %%E in ("%%D\ffmpeg-*") do set "PATH=%%E\bin;%PATH%"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u "%~dp0nlc.py" %*
