"""Aurora theme for the fund dashboard — no auth, no login, single user.

Ported from the `feature/dashboard-auth` branch's `_auth.py`, stripped of every
authentication concern: the login-card styling and the owner/role gating are
gone, only the visual layer remains.

The dashboard's own "Swiss luxury" CSS is CSS-variable-driven, so the recolor
works by overriding those variables (gold -> violet, warmer greys -> charcoal)
plus a few extras for buttons/links/alerts/sliders. Call :func:`apply_theme`
AFTER a page's own ``st.markdown`` CSS block so these rules win.

Every page should do, right after its ``st.set_page_config``-and-CSS preamble::

    from _theme import apply_theme
    apply_theme()
"""

from __future__ import annotations

import glob
import os
import re

import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(_HERE, "assets", "logo.svg")


def _inject_chrome_css() -> None:
    """Hide Streamlit's dev chrome (Deploy button, hamburger, decoration bar,
    footer) — this is a product, not a dev preview.

    NB: hide the chrome *inside* the header, never the header itself. Streamlit
    renders the "reopen sidebar" control there, so `display:none` on the header
    makes collapsing the sidebar a one-way door: it disappears and there is no
    way to bring it back short of a reload."""
    st.markdown(
        """<style>
    [data-testid="stToolbar"],[data-testid="stDecoration"],
    [data-testid="stStatusWidget"],#MainMenu,footer{display:none !important;}
    /* keep the header as an empty transparent strip: it still hosts the
       sidebar-reopen button, but shows nothing of its own */
    [data-testid="stHeader"]{background:transparent !important;
        box-shadow:none !important;border:0 !important;}
    /* On a desktop width the nav is pinned open and cannot be collapsed.
       Streamlit's reopen affordance only materialises on hover over a 0x0
       wrapper, so a collapsed sidebar on a dark page reads as gone for good —
       and this dashboard is three pages, the nav has no reason to hide.
       Below 768px Streamlit's own overlay behaviour is left alone, so the
       collapse button stays available where the screen is actually narrow. */
    @media (min-width: 768px){
        [data-testid="stSidebar"]{
            display:block !important;visibility:visible !important;
            width:300px !important;min-width:300px !important;max-width:300px !important;
            margin-left:0 !important;transform:none !important;}
        [data-testid="stSidebar"][aria-expanded="false"]{
            width:300px !important;min-width:300px !important;margin-left:0 !important;}
        [data-testid="stSidebarContent"]{visibility:visible !important;opacity:1 !important;}
        [data-testid="stSidebarCollapseButton"]{display:none !important;}
    }
    </style>""",
        unsafe_allow_html=True,
    )


def apply_dashboard_theme() -> None:
    """Aurora recolor: override the Swiss-luxury CSS variables + extras."""
    st.markdown(
        """<style>
    :root{
        --bg-primary:#0b0d12 !important; --bg-card:#141722 !important; --bg-elevated:#1c2233 !important;
        --text-primary:#eef0f4 !important; --text-secondary:#98a0b2 !important; --text-muted:#5c6478 !important;
        --accent-gold:#8b6cff !important; --accent-gold-dim:rgba(139,108,255,.15) !important;
        --accent-gold-glow:rgba(139,108,255,.10) !important;
        --border-subtle:#222738 !important; --border-faint:#1a1f2e !important;}
    .stApp,[data-testid="stAppViewContainer"]{
        background:radial-gradient(1200px 640px at 50% -18%,#141038 0%,#0a0b10 55%) !important;}
    [data-testid="stSidebar"]{background:#0d0f16 !important;border-right:1px solid #1a1f2e !important;}
    [data-testid="stSidebarNav"]{display:none !important;}  /* raw file-name nav -> custom nav */
    a,a:visited{color:#8b6cff !important;}
    /* buttons: dark by default, violet for primary */
    .stButton>button,.stDownloadButton>button{
        background:#141722 !important;border:1px solid #2a3042 !important;color:#e7e9ef !important;
        border-radius:10px !important;transition:border-color .15s,filter .15s !important;}
    .stButton>button:hover,.stDownloadButton>button:hover{border-color:#8b6cff !important;color:#fff !important;}
    .stButton>button[kind="primary"]{
        background:linear-gradient(180deg,#8b6cff,#7452ff) !important;border:0 !important;color:#fff !important;
        box-shadow:0 8px 22px rgba(124,92,255,.30) !important;}
    /* unify alert banners to the theme (kill the default blue info wash) */
    [data-testid="stAlert"],[data-testid="stAlertContainer"],[data-testid="stNotification"]{
        background:#141722 !important;border:1px solid #222738 !important;
        border-left:3px solid #8b6cff !important;border-radius:12px !important;}
    [data-testid="stAlert"]>div,[data-testid="stAlertContainer"]>div{background:transparent !important;}
    /* slider accents */
    [data-testid="stSlider"] [role="slider"]{
        background:#8b6cff !important;box-shadow:0 0 0 .2rem rgba(139,108,255,.25) !important;}
    [data-testid="stSlider"] [data-testid="stThumbValue"]{color:#8b6cff !important;}
    </style>""",
        unsafe_allow_html=True,
    )


_NAV_ICONS = {
    "Dashboard": ":material/space_dashboard:",
    "Fund Performance": ":material/monitoring:",
    "Backtest": ":material/insights:",
}


def _pretty_page(path: str) -> str:
    """``pages/2_Fund_Performance.py`` -> ``Fund Performance``."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"^\d+[_-]?", "", stem)
    return stem.replace("_", " ").strip().title()


def render_nav() -> None:
    """Custom sidebar nav (the default file-name nav is hidden by CSS above).
    Gives the main page a real 'Dashboard' label instead of 'app', plus icons."""
    with st.sidebar:
        try:
            st.page_link("app.py", label="Dashboard", icon=_NAV_ICONS["Dashboard"])
        except Exception:
            pass
        for f in sorted(glob.glob(os.path.join(_HERE, "pages", "*.py"))):
            label = _pretty_page(f)
            try:
                st.page_link(f"pages/{os.path.basename(f)}", label=label,
                             icon=_NAV_ICONS.get(label, ":material/description:"))
            except Exception:
                pass
        st.divider()


def apply_theme(nav: bool = True) -> None:
    """One call per page: hide dev chrome, recolor, set the sidebar logo, and
    render the custom nav. Safe to call on any page; failures never block the
    dashboard from rendering."""
    _inject_chrome_css()
    apply_dashboard_theme()
    if os.path.exists(LOGO_PATH):
        try:
            st.logo(LOGO_PATH, size="large")
        except Exception:
            pass
    if nav:
        render_nav()
