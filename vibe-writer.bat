@echo off
REM Run the Python script using uv from the current batch file directory
REM This ensures the script runs from the correct location regardless of where the bat file is called from
REM Start with terminal minimized to reduce visual clutter

REM Change to the directory where this batch file is located
cd /d "%~dp0"

REM Run the Python script using uv with minimized window
start /min powershell -Command "uv run run.py"

REM No pause needed since we're starting minimized - the window will close automatically when done
