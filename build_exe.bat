@echo off
rem ===========================================================================
rem  Build LocalVault AI. The file is still "Local Chat.exe" so the MSI install path can stay.
rem
rem    build_exe.bat            -> dist\Local Chat\Local Chat.exe   (folder build, fast start; recommended)
rem    build_exe.bat onefile    -> dist\Local Chat.exe              (single file, slower start)
rem
rem  Uses a private venv (.venv-build) so your system Python stays untouched.
rem  Set LOCALCHAT_BUILD_PYTHON=C:\path\to\python.exe to force a specific Python.
rem  Set LOCALCHAT_NOPAUSE=1 to skip the final "press any key".
rem ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0" || goto :fail
title Building LocalVault AI

set "MODE=onedir"
set "LOCALCHAT_ONEFILE="
if /i "%~1"=="onefile" set "MODE=onefile"
if /i "%~1"=="onefile" set "LOCALCHAT_ONEFILE=1"
set "OUTEXE=dist\Local Chat\Local Chat.exe"
if "%MODE%"=="onefile" set "OUTEXE=dist\Local Chat.exe"

rem ---------------------------------------------------------------- find Python 3.10+
set "PYEXE="
set "PYARGS="
if defined LOCALCHAT_BUILD_PYTHON set "PYEXE=%LOCALCHAT_BUILD_PYTHON%"
if not defined PYEXE if exist "%ProgramFiles%\Python313\python.exe" set "PYEXE=%ProgramFiles%\Python313\python.exe"
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PYEXE goto :havepy
where py >nul 2>nul
if not errorlevel 1 (
    set "PYEXE=py"
    set "PYARGS=-3"
    goto :havepy
)
where python >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
if not defined PYEXE (
    echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/windows/
    goto :fail
)
:havepy
"%PYEXE%" %PYARGS% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] "%PYEXE%" %PYARGS% is missing or older than Python 3.10.
    goto :fail
)
echo Using: "%PYEXE%" %PYARGS%
"%PYEXE%" %PYARGS% --version

rem ---------------------------------------------------------------- sanity checks
for %%F in (server.py desktop.py lan.py make_icon.py LocalChat.spec requirements-desktop.txt index.html app.js style.css favicon.svg apple-touch-icon.png manifest.webmanifest) do (
    if not exist "%%F" (
        echo [ERROR] %%F is missing. Run this script from the local-chat-app folder.
        goto :fail
    )
)
if not exist "vendor\" (
    echo [ERROR] vendor folder is missing.
    goto :fail
)

tasklist /FI "IMAGENAME eq Local Chat.exe" 2>nul | find /I "Local Chat.exe" >nul
if not errorlevel 1 (
    echo [ERROR] Local Chat.exe is running. Close the LocalVault AI window first, then run this again.
    goto :fail
)

set "WV2="
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul && set "WV2=1"
reg query "HKCU\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul && set "WV2=1"
if not defined WV2 echo [WARN] WebView2 Runtime not detected. The app will fall back to an Edge app window.

rem ---------------------------------------------------------------- venv + dependencies
set "VENV=%CD%\.venv-build"
set "VPY=%VENV%\Scripts\python.exe"
if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>nul || rmdir /s /q "%VENV%"
)
if exist "%VPY%" goto :havevenv
echo Creating build venv: "%VENV%"
"%PYEXE%" %PYARGS% -m venv "%VENV%"
if errorlevel 1 goto :fail_venv
:havevenv
echo Installing build tools (pyinstaller, pywebview, pillow) ...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
if errorlevel 1 goto :fail_pip
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements-desktop.txt
if errorlevel 1 goto :fail_pip

rem ---------------------------------------------------------------- icon + build
"%VPY%" make_icon.py
if errorlevel 1 goto :fail

echo Building (%MODE%) ...
"%VPY%" -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build LocalChat.spec
if errorlevel 1 goto :fail
if not exist "%OUTEXE%" (
    echo [ERROR] Build finished but "%OUTEXE%" was not created.
    goto :fail
)

echo.
echo ===========================================================================
echo  Done: %CD%\%OUTEXE%
echo.
echo  The window title is LocalVault AI. The file name stays Local Chat.exe.
echo  Chats stay in %LOCALAPPDATA%\LocalChat\data
echo  To refresh Desktop and Start menu names without moving the program:
echo    powershell -ExecutionPolicy Bypass -File "%~dp0deploy\Rename-LocalVaultShortcuts.ps1"
echo  Logs: %LOCALAPPDATA%\LocalChat\data\localchat.log
echo ===========================================================================
if not defined LOCALCHAT_NOPAUSE pause
endlocal
exit /b 0

:fail_venv
echo [ERROR] Could not create the venv. Try: "%PYEXE%" %PYARGS% -m ensurepip
goto :fail
:fail_pip
echo [ERROR] pip install failed. Check your internet connection or proxy, then run this again.
goto :fail
:fail
echo.
echo Build FAILED.
if not defined LOCALCHAT_NOPAUSE pause
endlocal
exit /b 1
