"""Streamlit Cloud entrypoint — runs options_agent/dashboard/app.py."""

import os
import sys
import runpy

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "options_agent"))
sys.path.insert(0, os.getcwd())

runpy.run_path("dashboard/app.py", run_name="__main__")
