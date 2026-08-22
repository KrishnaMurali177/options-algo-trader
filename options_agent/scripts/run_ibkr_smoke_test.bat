@echo off
REM Wrapper for the IBKR smoke test — used by Windows Task Scheduler.
REM Sets IBKR connection env vars, cd's into options_agent, runs the test,
REM and appends output to a log for later review.

set IBKR_HOST=127.0.0.1
set IBKR_PORT=4002
set IBKR_CLIENT_ID_BASE=42
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "c:\Users\Brat\OneDrive\Desktop\Algooooo\options-algo-trader\options_agent"

REM Timestamp the log so successive runs don't clobber each other.
for /f "tokens=1-4 delims=/ " %%a in ('date /t') do set d=%%d-%%b-%%c
for /f "tokens=1-2 delims=: " %%a in ('time /t') do set t=%%a%%b

py scripts\test_ibkr_paper_order.py > "logs\ibkr_smoke_%d%_%t%.log" 2>&1
