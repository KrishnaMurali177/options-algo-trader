"""Owner-only admin: generate/manage one-time invite links."""
import os
import sys

import streamlit as st

st.set_page_config(page_title="Admin", page_icon="🛠️", layout="wide")

_here = os.path.dirname(os.path.abspath(__file__))
_dash = os.path.dirname(_here)
if _dash not in sys.path:
    sys.path.insert(0, _dash)

from _auth import render_admin, require_auth  # noqa: E402

require_auth()
st.title("🛠️ Admin")
render_admin()
