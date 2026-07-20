@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Yuwang startup launcher
echo ========================================
echo.
echo Checking Docker, configuration and ports...

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Windows PowerShell was not found.
    echo Run .\yuwang.ps1 start from PowerShell instead.
    pause
    exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0yuwang.ps1" start %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Startup failed. See the details above; this window stays open.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo [OK] Service is ready. Opening the browser...
start "" "http://localhost:8080"
echo If the browser did not open, visit: http://localhost:8080
echo Closing this window does not stop Docker. Use .\yuwang.ps1 stop to stop it.
pause
exit /b 0
