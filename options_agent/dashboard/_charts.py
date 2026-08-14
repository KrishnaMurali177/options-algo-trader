"""Shared Plotly styling for the fund dashboard — Aurora dark.

One place that decides how every chart on the dashboard looks and behaves, so
the pages only describe *what* they are plotting.

Palette provenance: the series slots and the P&L sign pair were validated with
the dataviz palette validator against this dashboard's dark surfaces
(#141722 card, #0b0d12 page):

  series blue/orange/aqua  -> all checks PASS (all-pairs, dark)
  sign   good/red          -> PASS, CVD deutan dE 7.1 (6-8 band)

The sign pair sits in the "legal only with secondary encoding" band, which is
satisfied here: sign is also carried by bar direction from the zero baseline,
by the signed value in the tooltip, and by the table view underneath.
"""

from __future__ import annotations

import plotly.graph_objects as go

# ── Ink & chrome (one shade off the surface — hairlines, never dashes) ────────
INK = "#eef0f4"
INK_MUTED = "#98a0b2"
GRID = "#1a1f2e"
AXIS = "#222738"
ZERO = "#2a3042"
SPIKE = "#3a4152"
CARD = "#141722"

# ── Categorical series slots — fixed order, never cycled ─────────────────────
SERIES = {
    "paper": "#3987e5",     # slot 1, blue
    "live": "#d95926",      # slot 2, orange
    "combined": "#199e70",  # slot 3, aqua
}

# ── P&L sign (polarity) ──────────────────────────────────────────────────────
POS = "#0ca30c"
NEG = "#e66767"

FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

# Modebar: keep zoom/pan/reset, drop the export-and-lasso clutter.
CONFIG = {
    "displaylogo": False,
    "scrollZoom": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d",
                               "toggleSpikelines", "hoverClosestCartesian",
                               "hoverCompareCartesian"],
    "toImageButtonOptions": {"format": "png", "scale": 2},
}

# For charts where zooming buys nothing — a dozen weekly bars are all on screen
# already, so the zoom/pan controls are just clutter over the data.
CONFIG_STATIC = {
    "displayModeBar": False,
    "displaylogo": False,
    "scrollZoom": False,
    "staticPlot": False,   # keep hover; it is only the zoom controls we drop
}


def style(fig: go.Figure, title: str | None = None, height: int = 420,
          money_y: bool = True, crosshair: bool = True,
          legend: bool = True, y_fmt: str | None = None,
          zoom: bool = True) -> go.Figure:
    """Apply the shared look: transparent surface, hairline grid, crosshair +
    unified tooltip, dollar-formatted y axis. Height includes the axis band."""
    # NB: chart titles live in the page as Streamlit headings, not in the
    # figure — a plotly title and a top legend share the same margin band and
    # collide. `title` here is accepted and ignored so callers can document
    # what the chart is; pass it to st.subheader instead.
    fig.update_layout(
        height=height,
        font=dict(family=FONT, size=13, color=INK_MUTED),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        # right margin leaves room for the end-of-line direct labels
        margin=dict(t=34 if legend else 12, b=44, l=8, r=76),
        hovermode="x unified" if crosshair else "closest",
        hoverlabel=dict(bgcolor=CARD, bordercolor=ZERO, align="left",
                        font=dict(family=FONT, size=12, color=INK)),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left",
                    x=0, bgcolor="rgba(0,0,0,0)", font=dict(size=12)),
        dragmode="zoom" if zoom else False,
        barcornerradius=4,
    )
    fig.update_xaxes(
        showgrid=False, showline=True, linecolor=AXIS, linewidth=1,
        ticks="outside", tickcolor=AXIS, ticklen=4,
        tickfont=dict(size=12, color=INK_MUTED),
        showspikes=crosshair, spikemode="across", spikesnap="cursor",
        spikecolor=SPIKE, spikethickness=1, spikedash="solid",
    )
    fig.update_yaxes(
        showgrid=True, gridcolor=GRID, gridwidth=1, zeroline=True,
        zerolinecolor=ZERO, zerolinewidth=1, showline=False,
        tickfont=dict(size=12, color=INK_MUTED),
        tickprefix="$" if money_y else None, separatethousands=money_y,
        # explicit format: plotly's default SI ticks ("$10k") read inconsistently
        # next to the exact end-of-line labels ("$9,148")
        tickformat=y_fmt or (",.0f" if money_y else None),
    )
    return fig


def trend(series: list[tuple], height: int = 360, fmt: str = ",.0f",
          show_y: bool = False) -> go.Figure:
    """A brokerage-app trend chart: no gridlines, no y axis, no frame — just the
    curve, a dashed break-even reference, and a full-height hairline on hover.

    The number lives in the hero figure above the chart and in the hover
    readout, so the y axis has nothing left to say and is dropped entirely; the
    table below the chart remains the exact-values view.

    `series` is a list of (name, color, x, y). A single series is colored by the
    sign of where it ends (green up / red down); comparisons keep their fixed
    categorical slots so identity never follows rank."""
    fig = go.Figure()
    solo = len(series) == 1
    for name, color, x, y in series:
        xs, ys = list(x), list(y)
        if solo:
            color = POS if (ys and ys[-1] >= 0) else NEG
        fig.add_trace(go.Scatter(
            x=xs, y=ys, name=name, mode="lines",
            line=dict(color=color, width=2, shape="linear"),
            fill="tozeroy" if solo else None,
            fillcolor=_wash(color, 0.10) if solo else None,
            hovertemplate=f"{name}: <b>%{{y:${fmt}}}</b><extra></extra>",
            marker=dict(size=8, color=color, line=dict(width=2, color=CARD)),
        ))
    fig.update_layout(
        height=height,
        font=dict(family=FONT, size=13, color=INK_MUTED),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=28 if not solo else 8, b=28, l=8, r=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor=CARD, bordercolor=ZERO, align="left",
                        font=dict(family=FONT, size=12, color=INK)),
        showlegend=not solo,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left",
                    x=0, bgcolor="rgba(0,0,0,0)", font=dict(size=12)),
        dragmode=False,
    )
    fig.update_xaxes(
        showgrid=False, showline=False, zeroline=False, ticks="",
        tickfont=dict(size=11, color=INK_MUTED), nticks=6,
        showspikes=True, spikemode="across", spikesnap="cursor",
        spikecolor=SPIKE, spikethickness=1, spikedash="solid",
    )
    # Normally no y axis at all: the hero figure and the hover readout carry the
    # number. `show_y` is for pages with no hero — a backtest in per-contract
    # cents has nothing else to give the reader a magnitude.
    if show_y:
        fig.update_yaxes(showgrid=True, gridcolor=GRID, gridwidth=1,
                         showline=False, showticklabels=True, zeroline=False,
                         tickfont=dict(size=12, color=INK_MUTED),
                         tickprefix="$", tickformat=fmt)
        fig.update_layout(margin=dict(t=28 if not solo else 8, b=28, l=8, r=8))
    else:
        fig.update_yaxes(showgrid=False, showline=False, showticklabels=False,
                         zeroline=False)
    # A dashed break-even rule — a threshold, which is the one thing dashing is
    # actually for, not a gridline.
    fig.add_hline(y=0, line=dict(color=ZERO, width=1, dash="dot"))
    return fig


def line(fig: go.Figure, x, y, name: str, color: str, fill: bool = True,
         label_end: bool = True, fmt: str = ",.0f") -> go.Figure:
    """A 2px series line with a soft area wash, hover markers, and a selective
    direct label on the final point (never a number on every point).

    `fmt` is a Python/d3 number format shared by the tooltip and the end label —
    dollars-and-cents series (a per-contract backtest) pass ",.4f"."""
    fig.add_trace(go.Scatter(
        x=list(x), y=list(y), name=name, mode="lines",
        line=dict(color=color, width=2, shape="linear"),
        fill="tozeroy" if fill else None,
        fillcolor=_wash(color) if fill else None,
        hovertemplate=f"{name}: <b>%{{y:${fmt}}}</b><extra></extra>",
        hoverlabel=dict(bgcolor=CARD),
        marker=dict(size=8, color=color,
                    line=dict(width=2, color=CARD)),  # 2px surface ring
    ))
    xs, ys = list(x), list(y)
    if label_end and xs:
        fig.add_annotation(
            x=xs[-1], y=ys[-1], text=f"<b>${ys[-1]:{fmt}}</b>",
            showarrow=False, xanchor="left", xshift=8, yanchor="middle",
            font=dict(family=FONT, size=12, color=INK), bgcolor="rgba(0,0,0,0)",
        )
    return fig


def sign_bars(x, y, hover_label: str = "P&L") -> go.Figure:
    """Bars colored by sign, with the best/worst bar direct-labeled. Sign is
    also carried by bar direction and the tooltip value."""
    vals = list(y)
    colors = [POS if v >= 0 else NEG for v in vals]
    text = [""] * len(vals)
    if vals:
        for i in (vals.index(max(vals)), vals.index(min(vals))):
            text[i] = f"${vals[i]:,.0f}"
    fig = go.Figure(go.Bar(
        x=list(x), y=vals, marker_color=colors,
        text=text, textposition="outside", cliponaxis=False,
        textfont=dict(family=FONT, size=11, color=INK),
        hovertemplate=f"{hover_label}: <b>%{{y:$,.0f}}</b><extra></extra>",
    ))
    fig.update_layout(bargap=bar_gap(len(vals)))
    fig.update_traces(width=bar_width(len(vals)))
    return fig


def bar_gap(n: int) -> float:
    """Surface gap between adjacent bars. A handful of categories stretched to
    fill the width reads as thick saturated blocks, so widen the gap instead."""
    if n <= 3:
        return 0.7
    if n <= 6:
        return 0.5
    return 0.28


def bar_width(n: int):
    """Explicit bar width, in category units, when there are few categories.

    `bargap` alone does not save a one- or two-category chart: plotly still
    spreads the slots across the full width, so a single bar renders as a
    300px saturated slab. None = let plotly decide."""
    if n <= 2:
        return 0.22
    if n <= 4:
        return 0.4
    return None


def hero_html(value: float, label: str, sub: str = "", fmt: str = ",.0f") -> str:
    """The brokerage-app hero figure: the number the page is actually about,
    in the system sans with proportional figures, sign-colored, with the
    period/account it covers underneath."""
    color = POS if value >= 0 else NEG
    sign = "+" if value > 0 else ""
    sub_html = (f'<div style="color:{INK_MUTED};font-size:.82rem;margin-top:.35rem">'
                f'{sub}</div>') if sub else ""
    return (
        f'<div style="margin:.25rem 0 1.1rem">'
        f'<div style="color:{INK_MUTED};font-size:.78rem;font-weight:500;'
        f'letter-spacing:.06em;text-transform:uppercase">{label}</div>'
        f'<div style="color:{color};font-family:{FONT};font-size:2.9rem;'
        f'font-weight:700;line-height:1.15;letter-spacing:-.02em">'
        f'{sign}${value:{fmt}}</div>{sub_html}</div>'
    )


def _wash(hex_color: str, alpha: float = 0.13) -> str:
    """Series color at low alpha for the area fill under a line."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
