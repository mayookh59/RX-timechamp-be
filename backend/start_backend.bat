@echo off
REM Wrapper that starts the TrackMe backend in the background.
REM Used by the "TrackMe Backend" scheduled task.
cd /d "%~dp0"
python start_local.py
