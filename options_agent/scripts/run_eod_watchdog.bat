@echo off
REM EOD safety-net watchdog (called by Windows Task Scheduler ~15:28 ET).
REM Runs as an INDEPENDENT process from the trading agents so it force-closes
REM today-expiring options and flags orphaned stock even if an agent has crashed.
REM Loops until 15:45 ET, retrying any failed close.

cd /d "c:\Users\krish\options-algo-trader\options_agent"

set LOGFILE=logs\eod_watchdog_%date:~10,4%-%date:~4,2%-%date:~7,2%.log

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

"..\venv\Scripts\python.exe" -u scripts\eod_watchdog.py --loop --interval 60 >> "%LOGFILE%" 2>&1
