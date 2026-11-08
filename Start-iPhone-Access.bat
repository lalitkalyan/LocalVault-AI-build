@echo off
setlocal EnableExtensions
cd /d "%~dp0" || exit /b 1
title LocalVault AI (iPhone access)
echo.
echo  LocalVault AI will allow a paired iPhone on this PC's private Wi-Fi.
echo  Chats stay on this PC. Ollama is not opened to the network.
echo  The pairing code appears in the LocalVault AI window, not in this console.
echo  Close the LocalVault AI window to stop iPhone access and revoke phones.
echo  The program file is still Local Chat.exe.
echo.
echo  If Safari cannot connect, open an Administrator PowerShell and run:
echo    powershell -ExecutionPolicy Bypass -File "%~dp0deploy\Enable-LocalChatLanFirewall.ps1"
echo.
if exist "%~dp0Local Chat.exe" (
  "%~dp0Local Chat.exe" --lan %*
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)
if exist "%~dp0dist\Local Chat\Local Chat.exe" (
  "%~dp0dist\Local Chat\Local Chat.exe" --lan %*
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0desktop.py" --lan %*
  if errorlevel 1 pause
  exit /b %ERRORLEVEL%
)
python "%~dp0desktop.py" --lan %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
