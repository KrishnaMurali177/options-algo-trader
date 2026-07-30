"""Offline RL / contextual-bandit layer over the 0DTE replay engine.

This package is a *controller* on top of the existing strategy: it does not
change any entry/exit logic, it only chooses which parameter preset (or none at
all) to run for a given symbol on a given day. Nothing here is imported by the
live agent — see options_agent/scripts/run_rl_bandit.py for the entry point.
"""
