REM Copyright (c) 2026 OpenStorey LLC. All rights reserved.
REM Licensed under the AutoYou Source-Available License.
REM See LICENSE in the project root for license information.
REM AI/ML training use prohibited without written authorization (License S3.9).
REM AUTOYOU-PROVENANCE-H-revenue-7c34670d1118159559f0daa2

@echo off
:: Copyright (c) 2026 OpenStorey LLC. All rights reserved.
:: Licensed under the AutoYou Source-Available License.
:: See LICENSE in the project root for license information.
setlocal

cd /d "%~dp0"

set "AUTOYOU_BUILD_VERSION=81.0.0"
if exist "%~dp0VERSION" (
    set /p AUTOYOU_BUILD_VERSION=<"%~dp0VERSION"
)
echo [INFO] AutoYou v%AUTOYOU_BUILD_VERSION% Launcher

set "PYTHON_CMD="
call :try_python "py -3.13"
call :try_python "py -3.12"
call :try_python "py -3.11"
call :try_python "py -3.10"
call :try_python "py -3"
call :try_python "python"

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10+ is required.
    echo [INFO] Install Python from https://python.org/ and make sure it is on PATH.
    exit /b 1
)

:: Keep this batch process attached to bootstrap; bootstrap owns the server
:: process group and propagates graceful shutdown/parent ownership to it.
:: The ordinary launcher is expected to be browser-capable. The bootstrap
:: profile remains configurable, and an explicit --without internet still
:: opts the component out.
call %PYTHON_CMD% scripts\bootstrap_autoyou.py --with internet %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:try_python
if defined PYTHON_CMD goto :eof
%~1 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PYTHON_CMD=%~1"
goto :eof
