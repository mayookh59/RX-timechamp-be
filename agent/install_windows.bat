@echo off
REM TrackMe Production Installer for Windows
REM
REM Installs the agent as a Scheduled Task that:
REM   - Runs at user logon (so it has GUI access for screenshots + tray)
REM   - Restarts automatically every 1 minute on failure (up to 999 times)
REM   - Has highest privileges available to the user
REM   - Survives reboots, logoffs, sleep/wake
REM
REM Usage:
REM   install_windows.bat [SERVER_URL] [USER_EMAIL]
REM Example:
REM   install_windows.bat http://192.168.31.253:8000/api/v1 alice@company.com
REM
REM Run as Administrator for proper installation.

setlocal EnableDelayedExpansion

set "SERVER_URL=%~1"
set "USER_EMAIL=%~2"
if "%SERVER_URL%"=="" set "SERVER_URL=http://192.168.31.253:8000/api/v1"
if "%USER_EMAIL%"=="" set "USER_EMAIL=user@company.com"

set "DATA_DIR=%PROGRAMDATA%\TrackMe"
set "AGENT_PY=%DATA_DIR%\trackme_agent.py"
set "CONFIG_JSON=%DATA_DIR%\config.json"
set "LOG_FILE=%USERPROFILE%\Desktop\TrackMe-Install.log"
set "TASK_NAME=TrackMe Agent"

echo ============================================ > "%LOG_FILE%"
echo TrackMe Agent Installer - %DATE% %TIME%      >> "%LOG_FILE%"
echo ============================================ >> "%LOG_FILE%"

echo.
echo  ====================================================
echo    TrackMe Agent Installer (Production)
echo  ====================================================
echo    Server:  %SERVER_URL%
echo    User:    %USER_EMAIL%
echo    Data:    %DATA_DIR%
echo    Log:     %LOG_FILE%
echo  ====================================================
echo.

REM ── 1. Check Python ─────────────────────────────────────────────
echo [1/7] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo        Python not found. Installing Python 3.11...
    if exist "%TEMP%\python_setup.exe" del /f /q "%TEMP%\python_setup.exe"
    powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile '%TEMP%\python_setup.exe'" >> "%LOG_FILE%" 2>&1
    if not exist "%TEMP%\python_setup.exe" (
        echo  ERROR: Python download failed. Check internet connection.
        echo Python download failed >> "%LOG_FILE%"
        goto fail
    )
    "%TEMP%\python_setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1
    del /f /q "%TEMP%\python_setup.exe" >nul 2>&1
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python311;%LOCALAPPDATA%\Programs\Python\Python311\Scripts;%PATH%"
    python --version >nul 2>&1
    if errorlevel 1 (
        echo  ERROR: Python install failed. Visit https://python.org/downloads
        echo Python install failed >> "%LOG_FILE%"
        goto fail
    )
    echo        Python installed.
) else (
    echo        Python found:
    python --version
)

REM ── 2. Create data directory ─────────────────────────────────────
echo [2/7] Creating data directory...
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%" 2>>"%LOG_FILE%"
if not exist "%DATA_DIR%" (
    echo  ERROR: Could not create %DATA_DIR%. Run as Administrator.
    echo mkdir failed for %DATA_DIR% >> "%LOG_FILE%"
    goto fail
)

REM Make data dir writable by current user (so logs work without admin)
icacls "%DATA_DIR%" /grant "%USERNAME%:(OI)(CI)F" /T >>"%LOG_FILE%" 2>&1

REM ── 3. Download agent script ─────────────────────────────────────
echo [3/7] Downloading agent script from %SERVER_URL%/agents/download-script ...
powershell -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%SERVER_URL%/agents/download-script' -OutFile '%AGENT_PY%' -UseBasicParsing" >>"%LOG_FILE%" 2>&1
if not exist "%AGENT_PY%" (
    echo  ERROR: Failed to download agent script. Check that backend is reachable.
    echo Agent download failed >> "%LOG_FILE%"
    goto fail
)

REM ── 4. Write config ──────────────────────────────────────────────
echo [4/7] Writing config...
(
    echo {
    echo   "api_base_url": "%SERVER_URL:\=\\%",
    echo   "user_email":   "%USER_EMAIL%",
    echo   "idle_threshold_seconds":      60,
    echo   "sync_interval_seconds":       60,
    echo   "screenshot_interval_seconds": 120,
    echo   "log_level": "INFO"
    echo }
) > "%CONFIG_JSON%"

REM ── 5. Install Python deps ───────────────────────────────────────
echo [5/7] Installing Python dependencies...
python -m pip install --quiet --upgrade pip >>"%LOG_FILE%" 2>&1
python -m pip install --quiet requests psutil pillow pystray >>"%LOG_FILE%" 2>&1

REM ── 6. Remove old startup mechanisms ─────────────────────────────
echo [6/7] Removing old startup shims...
if exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TrackMeAgent.bat" (
    del /f "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TrackMeAgent.bat" >>"%LOG_FILE%" 2>&1
    echo        Removed startup shim
)
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM Kill any lingering agent processes from previous installs
for /f "tokens=2 delims==" %%i in ('wmic process where "commandline like '%%trackme_agent%%' and (name='pythonw.exe' or name='python.exe')" get processid /value 2^>nul ^| findstr "ProcessId"') do (
    taskkill /F /PID %%i >>"%LOG_FILE%" 2>&1
)

REM ── 7. Install Scheduled Task ────────────────────────────────────
echo [7/7] Installing Scheduled Task with restart-on-failure...

REM Find pythonw.exe
where pythonw >nul 2>&1
if errorlevel 1 (
    set "PYTHONW=python"
) else (
    for /f "delims=" %%i in ('where pythonw') do set "PYTHONW=%%i"
)
echo Using Python: !PYTHONW! >> "%LOG_FILE%"

REM Build the Scheduled Task XML — gives us:
REM   - Trigger: at user logon
REM   - Restart on failure: every 1 minute, up to 999 times
REM   - Run only when user is logged in
REM   - No time limit (runs forever)
set "TASK_XML=%DATA_DIR%\task.xml"
(
    echo ^<?xml version="1.0" encoding="UTF-16"?^>
    echo ^<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
    echo   ^<RegistrationInfo^>
    echo     ^<Description^>TrackMe Activity Tracking Agent^</Description^>
    echo     ^<Author^>TrackMe^</Author^>
    echo   ^</RegistrationInfo^>
    echo   ^<Triggers^>
    echo     ^<LogonTrigger^>
    echo       ^<Enabled^>true^</Enabled^>
    echo       ^<UserId^>%USERNAME%^</UserId^>
    echo     ^</LogonTrigger^>
    echo   ^</Triggers^>
    echo   ^<Principals^>
    echo     ^<Principal id="Author"^>
    echo       ^<UserId^>%USERNAME%^</UserId^>
    echo       ^<LogonType^>InteractiveToken^</LogonType^>
    echo       ^<RunLevel^>LeastPrivilege^</RunLevel^>
    echo     ^</Principal^>
    echo   ^</Principals^>
    echo   ^<Settings^>
    echo     ^<MultipleInstancesPolicy^>IgnoreNew^</MultipleInstancesPolicy^>
    echo     ^<DisallowStartIfOnBatteries^>false^</DisallowStartIfOnBatteries^>
    echo     ^<StopIfGoingOnBatteries^>false^</StopIfGoingOnBatteries^>
    echo     ^<AllowHardTerminate^>true^</AllowHardTerminate^>
    echo     ^<StartWhenAvailable^>true^</StartWhenAvailable^>
    echo     ^<RunOnlyIfNetworkAvailable^>false^</RunOnlyIfNetworkAvailable^>
    echo     ^<IdleSettings^>
    echo       ^<StopOnIdleEnd^>false^</StopOnIdleEnd^>
    echo       ^<RestartOnIdle^>false^</RestartOnIdle^>
    echo     ^</IdleSettings^>
    echo     ^<AllowStartOnDemand^>true^</AllowStartOnDemand^>
    echo     ^<Enabled^>true^</Enabled^>
    echo     ^<Hidden^>false^</Hidden^>
    echo     ^<RunOnlyIfIdle^>false^</RunOnlyIfIdle^>
    echo     ^<DisallowStartOnRemoteAppSession^>false^</DisallowStartOnRemoteAppSession^>
    echo     ^<UseUnifiedSchedulingEngine^>true^</UseUnifiedSchedulingEngine^>
    echo     ^<WakeToRun^>false^</WakeToRun^>
    echo     ^<ExecutionTimeLimit^>PT0S^</ExecutionTimeLimit^>
    echo     ^<Priority^>7^</Priority^>
    echo     ^<RestartOnFailure^>
    echo       ^<Interval^>PT1M^</Interval^>
    echo       ^<Count^>999^</Count^>
    echo     ^</RestartOnFailure^>
    echo   ^</Settings^>
    echo   ^<Actions Context="Author"^>
    echo     ^<Exec^>
    echo       ^<Command^>!PYTHONW!^</Command^>
    echo       ^<Arguments^>"%AGENT_PY%"^</Arguments^>
    echo       ^<WorkingDirectory^>%DATA_DIR%^</WorkingDirectory^>
    echo     ^</Exec^>
    echo   ^</Actions^>
    echo ^</Task^>
) > "%TASK_XML%"

REM Convert task XML to UTF-16 (schtasks requires it)
powershell -Command "Get-Content '%TASK_XML%' | Set-Content '%TASK_XML%' -Encoding Unicode" >>"%LOG_FILE%" 2>&1

schtasks /create /tn "%TASK_NAME%" /xml "%TASK_XML%" /f >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo  ERROR: Could not create Scheduled Task. See %LOG_FILE%.
    goto fail
)

REM Start it now
schtasks /run /tn "%TASK_NAME%" >>"%LOG_FILE%" 2>&1

REM Verify it's running
timeout /t 4 /nobreak >nul
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | findstr /I pythonw >nul
if errorlevel 1 (
    echo        Note: pythonw.exe not visible in task list yet.
    echo        Check %DATA_DIR%\agent.log in 60 seconds.
) else (
    echo        Agent process is running.
)

echo.
echo  ====================================================
echo    INSTALL COMPLETE
echo  ====================================================
echo    Task name:    %TASK_NAME%
echo    Agent script: %AGENT_PY%
echo    Config:       %CONFIG_JSON%
echo    Log:          %DATA_DIR%\agent.log
echo.
echo    Verify:       schtasks /query /tn "%TASK_NAME%"
echo    Stop:         schtasks /end   /tn "%TASK_NAME%"
echo    Uninstall:    schtasks /delete /tn "%TASK_NAME%" /f
echo  ====================================================
echo.
echo  Within ~60 seconds you should see this device on the dashboard.
echo.
pause
exit /b 0

:fail
echo.
echo  ====================================================
echo    INSTALL FAILED
echo  ====================================================
echo    Check the log: %LOG_FILE%
echo.
pause
exit /b 1
