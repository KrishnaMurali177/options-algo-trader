"""Streamlit Cloud entrypoint — runs options_agent/dashboard/app.py."""

import os
import sys

_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "options_agent")
os.chdir(_root)
if _root not in sys.path:
    sys.path.insert(0, _root)

_app_path = os.path.join(_root, "dashboard", "app.py")
with open(_app_path) as _f:
    exec(compile(_f.read(), _app_path, "exec"), {"__file__": _app_path, "__name__": "__main__"})
