@echo off
REM Daily entry point for the Sweet Spot live agent (called by Windows Task Scheduler).
REM Runs the agent in single-day mode; Task Scheduler handles the next day's invocation.

cd /d "c:\Users\krish\options-algo-trader\options_agent"

REM Build a timestamped log filename: logs\agent_2026-05-03.log
set LOGFILE=logs\agent_%date:~10,4%-%date:~4,2%-%date:~7,2%.log

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

"venv\Scripts\python.exe" -u scripts\run_sweet_spot_agent.py >> "%LOGFILE%" 2>&1
