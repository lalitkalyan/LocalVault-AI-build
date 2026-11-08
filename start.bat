@echo off
rem Start LocalVault AI on this PC only (127.0.0.1). For an iPhone, use Start-iPhone-Access.bat.
cd /d "%~dp0" & start "" http://127.0.0.1:8080 & (where py >nul 2>nul && py -3 server.py %* || python server.py %*)
