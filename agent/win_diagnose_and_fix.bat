@echo off
REM TrackMe Windows Agent - Diagnostic + Recovery
REM
REM Send this to Thinkpad (or any Windows user whose agent has stopped reporting).
REM Usage:
REM   win_diagnose_and_fix.bat [SERVER_IP]
REM Example:
REM   win_diagnose_and_fix.bat 192.168.31.253

setlocal EnableDelayedExpansion

set "SERVER_IP=%~1"
if "%SERVER_IP%"=="" set "SERVER_IP=192.168.31.253"

set "DATA_DIR=%PROGRAMDATA%\TrackMe"
set "CFG=%DATA_DIR%\config.json"
set "LOG=%DATA_DIR%\agent.log"
set "AGENT_PY=%DATA_DIR%\trackme_agent.py"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo ==========================================
echo   TrackMe Windows Agent Diagnostic
echo ==========================================

echo.
echo [1/8] Machine identity + current IP
hostname
ipconfig ^| findstr /i "IPv4"

echo.
echo [2/8] LAN reachability to server (%SERVER_IP%:8000)
curl -s -o NUL -w "  HTTP %%{http_code}  (time=%%{time_total}s)" --max-time 5 "http://%SERVER_IP%:8000/docs"
echo.
if errorlevel 1 (
    echo   !! CANNOT REACH SERVER - probably on wrong network or server is down
) else (
    echo   OK - server is reachable
)

echo.
echo [3/8] Is the agent process running?
tasklist /FI "IMAGENAME eq pythonw.exe" /V 2>NUL | findstr /I "trackme_agent" >NUL
if errorlevel 1 (
    tasklist /FI "IMAGENAME eq python.exe" /V 2>NUL | findstr /I "trackme_agent" >NUL
    if errorlevel 1 (
        echo   !! Agent process is NOT running
    ) else (
        echo   Running via python.exe (should be pythonw.exe)
        tasklist /FI "IMAGENAME eq python.exe" /V | findstr /I "trackme_agent"
    )
) else (
    echo   OK - agent running via pythonw.exe
    tasklist /FI "IMAGENAME eq pythonw.exe" /V | findstr /I "trackme_agent"
)

echo.
echo [4/8] Python availability
where python 2>NUL
where pythonw 2>NUL
python --version 2>NUL

echo.
echo [5/8] Required modules
for %%M in (requests psutil PIL pystray) do (
    python -c "import %%M" 2>NUL && (echo   OK   %%M) || (echo   MISS %%M - run: pip install requests psutil pillow pystray)
)

echo.
echo [6/8] Config file
if exist "%CFG%" (
    echo   Path: %CFG%
    type "%CFG%"
    echo.
) else (
    echo   !! Config missing at %CFG%
)

echo.
echo [7/8] Last 30 lines of agent.log
if exist "%LOG%" (
    powershell -NoProfile -Command "Get-Content -Tail 30 '%LOG%'"
) else (
    echo   !! agent.log missing at %LOG%
)

echo.
echo [8/8] Startup entry
if exist "%STARTUP%\TrackMeAgent.bat" (
    echo   Found startup shim: %STARTUP%\TrackMeAgent.bat
    type "%STARTUP%\TrackMeAgent.bat"
) else (
    echo   !! No startup shim - agent will NOT auto-start on login
)

echo.
echo ==========================================
echo   Recovery
echo ==========================================

REM 1. Rewrite api_base_url in config so the agent points at the right server
if exist "%CFG%" (
    echo Updating api_base_url in config to http://%SERVER_IP%:8000/api/v1 ...
    python -c "import json,sys; p=r'%CFG%'; c=json.load(open(p)); c['api_base_url']='http://%SERVER_IP%:8000/api/v1'; json.dump(c,open(p,'w'),indent=2); print('  Config updated')"
) else (
    echo   Skipping config update - file missing
)

REM 2. Kill any stale python processes running the agent
echo Killing any existing agent processes...
taskkill /F /FI "IMAGENAME eq pythonw.exe" /FI "WINDOWTITLE eq TrackMeAgent*" 2>NUL
for /f "tokens=2 delims==" %%i in ('wmic process where "commandline like '%%trackme_agent%%' and (name='pythonw.exe' or name='python.exe')" get processid /value 2^>NUL ^| findstr "ProcessId"') do (
    echo   Killing PID %%i
    taskkill /F /PID %%i 2>NUL
)

REM 3. Make sure the startup shim exists
if not exist "%STARTUP%\TrackMeAgent.bat" (
    echo Creating startup shim...
    (
        echo @echo off
        echo cd /d "%DATA_DIR%"
        echo start /b "TrackMeAgent" pythonw trackme_agent.py
    ) > "%STARTUP%\TrackMeAgent.bat"
    echo   Created: %STARTUP%\TrackMeAgent.bat
)

REM 4. Start the agent now
if exist "%AGENT_PY%" (
    echo Starting agent...
    cd /d "%DATA_DIR%"
    start /b "TrackMeAgent" pythonw trackme_agent.py
    timeout /t 3 /nobreak >NUL
    tasklist /FI "IMAGENAME eq pythonw.exe" /V 2>NUL | findstr /I "trackme_agent" >NUL && (
        echo   OK - agent process is now running
    ) || (
        echo   !! Agent failed to start - check %LOG% for errors
    )
) else (
    echo   !! Cannot start - %AGENT_PY% is missing. Reinstall the agent.
)

echo.
echo Tailing %LOG% for 10 seconds - look for 'Synced ... sessions' or warnings:
if exist "%LOG%" (
    powershell -NoProfile -Command "$job=Start-Job -ScriptBlock { Get-Content -Path '%LOG%' -Wait -Tail 0 }; Start-Sleep -Seconds 10; Stop-Job $job; Receive-Job $job; Remove-Job $job"
)

echo.
echo Done. Refresh the dashboard - agent should show 'online' within 1 minute.
echo (If still offline, copy this whole output and send it back.)
pause
endlocal
