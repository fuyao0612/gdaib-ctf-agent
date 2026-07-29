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

where docker.exe >nul 2>&1
if not errorlevel 1 (
    docker info >nul 2>&1
    if errorlevel 1 (
        if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
            echo Docker Desktop is not ready. Starting it now...
            start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
            echo Waiting for Docker Desktop to become ready. This can take up to 2 minutes...
            for /l %%N in (1,1,60) do (
                docker info >nul 2>&1
                if not errorlevel 1 goto docker_ready
                if %%N EQU 30 echo Still waiting for Docker Desktop...
                timeout /t 2 /nobreak >nul
            )
            echo.
            echo [ERROR] Docker Desktop did not become ready within 2 minutes.
            echo Please open Docker Desktop, wait until its engine is running, then double-click this file again.
            pause
            exit /b 1
        )
    )
)

:docker_ready

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0yuwang.ps1" start -Build -OpenBrowser
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Startup failed. Review the build or configuration details above; this window stays open.
    pause
    exit /b %EXIT_CODE%
)

echo.
echo [OK] Service is ready. The actual Web address is shown above.
echo If the browser did not open, use that address from this window.
echo Closing this window does not stop Docker. Use .\yuwang.ps1 stop to stop it.
pause
exit /b 0
