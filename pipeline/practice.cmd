@echo off
rem Launcher for the practice organizer (same PATH setup as nlc.cmd).
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*") do for /d %%E in ("%%D\ffmpeg-*") do set "PATH=%%E\bin;%PATH%"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u "%~dp0practice.py" %*
