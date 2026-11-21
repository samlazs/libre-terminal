"""Chart palette, layout defaults, annotations, and NBER recession shading."""

from __future__ import annotations

import composite as C

PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

INK = {"text": "#f0efec", "text2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835", "surface": "#1a1a19"}

UP, DOWN = "#0ca30c", "#d03b3b"


# ---------------------------------------------------------------- figures
def _layout(multi, **extra):
    lay = {
        "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": 'system-ui, -apple-system, "Segoe UI", sans-serif', "size": 11, "color": INK["muted"]},
        "margin": {"l": 52, "r": 16, "t": 28 if multi else 10, "b": 34},
        "colorway": PALETTE, "hovermode": "x unified",
        "hoverlabel": {"bgcolor": "#222220", "bordercolor": INK["axis"], "font": {"color": INK["text"], "size": 12}},
        "showlegend": multi,
        "legend": {"orientation": "h", "x": 0, "y": 1.02, "yanchor": "bottom", "font": {"color": INK["text2"], "size": 11}, "bgcolor": "rgba(0,0,0,0)"},
        "xaxis": {"showgrid": False, "linecolor": INK["axis"], "tickcolor": INK["axis"], "zeroline": False, "automargin": True},
        "yaxis": {"gridcolor": INK["grid"], "gridwidth": 1, "zerolinecolor": INK["axis"], "zerolinewidth": 1, "linecolor": "rgba(0,0,0,0)", "automargin": True},
    }
    lay.update(extra)
    return lay


def _note(text):
    """A short note inside the plot area (bottom right): never placed in the margin, where it would be clipped."""
    return {"text": text, "xref": "paper", "yref": "paper", "x": 1, "y": 0, "xanchor": "right", "yanchor": "bottom", "showarrow": False,
            "font": {"color": INK["muted"], "size": 10}, "bgcolor": "rgba(26,26,25,0.75)"}


RECESSION_FILL = "rgba(152,152,152,0.16)"


def recession_periods(points):
    """[(start, end)] from a monthly 0/1 recession indicator (FRED USREC): each run of 1s, from the first month of the
    run to the first day of the month after its last month."""
    out, start, last = [], None, None
    for d, v in sorted(points):
        if v and v >= 0.5:
            start = start or d[:10]
            last = d[:10]
        elif start:
            out.append((start, C._next_month(last)))
            start = None
    if start:
        out.append((start, C._next_month(last)))
    return out


def _recessions():
    """U.S. recessions as dated by the NBER (FRED USREC, monthly), kept 12 hours."""
    return C.cached(("recessions",), 12 * 3600, lambda: C.recession_periods(C._fred_points("USREC", "1854-01-01")))


def add_recession_shading(lay, x_from, x_to):
    """Shade U.S. recessions between x_from and x_to (ISO dates) on a date axis. Returns the source entries used."""
    try:
        periods = C._recessions()
    except C.CompositeError as e:
        lay.setdefault("annotations", []).append(dict(C._note(f"Recession shading unavailable: {str(e)[:80]}"), y=1, yanchor="top"))
        return []
    shapes = [{"type": "rect", "xref": "x", "yref": "paper", "x0": max(a, x_from), "x1": min(b, x_to), "y0": 0, "y1": 1,
               "fillcolor": RECESSION_FILL, "line": {"width": 0}, "layer": "below"} for a, b in periods if b > x_from and a < x_to]
    lay["shapes"] = lay.get("shapes", []) + shapes
    return [C.src("FRED", "Shaded: U.S. recessions as dated by the NBER (USREC, monthly)", "https://fred.stlouisfed.org/series/USREC")]


SHADE_OPTIONS = [{"label": "No shading", "value": "none"}, {"label": "U.S. recessions", "value": "rec"}]
