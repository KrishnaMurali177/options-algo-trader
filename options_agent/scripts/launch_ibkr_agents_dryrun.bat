@echo off
REM DRY-RUN variant of launch_ibkr_agents.bat. Adds --no-paper so no orders
REM are placed. Every other code path exercises identically to the real launch:
REM  - broker.set_broker("ibkr", symbol=...)
REM  - broker.fetch_bars (IBKR)
REM  - Scan loop, indicator build, trigger evaluation
REM  - broker.get_dte_chain (IBKR OPRA lookup) when triggers fire
REM  - Journal writes, Discord verdict notifications (rejects only)
REM
REM Use this to validate the whole wire on a live market day without spending
REM real (paper) capital.

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set ROOT=c:\Users\Brat\OneDrive\Desktop\Algooooo\options-algo-trader\options_agent

cd /d "%ROOT%"

REM Set AGENT_SYMBOL in each child cmd so the module-level logger names its
REM per-symbol log file (sweet_spot_agent_spy_YYYY-MM-DD.log etc.) instead of
REM the shared _agent_ file that would clobber across 4 processes.
start "SPY_dryrun" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=SPY" && py scripts\run_sweet_spot_agent.py --broker ibkr --symbol SPY --no-paper"
start "QQQ_dryrun" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=QQQ" && py scripts\run_sweet_spot_agent.py --broker ibkr --symbol QQQ --no-paper"
start "IWM_dryrun" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=IWM" && py scripts\run_sweet_spot_agent.py --broker ibkr --symbol IWM --no-paper"
start "DIA_dryrun" /MIN cmd /c "cd /d %ROOT% && set "AGENT_SYMBOL=DIA" && py scripts\run_sweet_spot_agent.py --broker ibkr --symbol DIA --no-paper"

echo Launched 4 IBKR DRY-RUN daemons. Check taskbar for minimized windows.
echo No orders will be placed. Check logs\ and sweet_spot_journal\*_verdicts.jsonl to inspect scans.
