@echo off
rem Launcher for the ATEM recording-name setter. Uses the Node that ships with Companion
rem (node22 if present, else whichever runtime Companion has).
set "NODE="
for /d %%D in ("%ProgramFiles%\Companion\resources\node-runtimes\node*") do if exist "%%D\node.exe" set "NODE=%%D\node.exe"
if exist "%ProgramFiles%\Companion\resources\node-runtimes\node22\node.exe" set "NODE=%ProgramFiles%\Companion\resources\node-runtimes\node22\node.exe"
if not defined NODE (echo No Node runtime found under Companion & exit /b 1)
"%NODE%" "%~dp0atem_name.js" %*
