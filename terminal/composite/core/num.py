"""The numeric boundary: every number read from a source passes num() (finite float or None) and every derived figure
with a known plausible range passes banded() (the value, or None with a note on the answer).

num(x)            a finite float, or None for None, bool, NaN/Inf (also the CSV strings 'nan', 'inf'), text that is not a
                  number, and the placeholders sources send for "no value" (FINRA's 999999.99).
finite(x)         a finite float or None, nothing else checked: for the serializer scrub and cache reads, which must not
                  change a number that is merely implausible.
banded(m, x)      x when it lies inside BANDS[m], else None, and the note "<label> left blank: <value> is outside the
                  plausible range <lo> to <hi>" is recorded on the request (and on every cache that holds the answer).
nonfinite_path(o) where in a view's answer the first NaN/Inf float sits ("results[3].pe"), or None.
"""

from __future__ import annotations

import contextvars
import math
import numbers
from typing import NamedTuple

# Values sources send in place of "no value". Only exact, never-real figures: FINRA last-sale yields (999999.99) and
# its 7-digit variant; a plain 999999 is a real share count or volume and is not listed.
PLACEHOLDERS = frozenset({999999.99, -999999.99, 9999999.99, -9999999.99})

_EMPTY_TEXT = frozenset({"", ".", "-", "–", "—", "n/a", "na", "nan", "null", "none"})


class Band(NamedTuple):
    lo: float
    hi: float
    label: str
    unit: str = ""          # shown after the value and the limits in the note
    scale: float = 1.0      # values are stored as x and shown as x * scale (a fraction against a percent band)
    lo_open: bool = False   # (lo, hi] instead of [lo, hi]


# metric -> plausible range. Outside it the figure is a source error (a tiny denominator, a unit slip, a bad print),
# never a market fact: it is blanked with a note instead of drawn.
BANDS: dict[str, Band] = {
    "ev_ebitda": Band(0, 200, "EV/EBITDA", "x", lo_open=True),
    "ev_sales": Band(0, 100, "EV/sales", "x", lo_open=True),
    "pb": Band(0, 200, "P/B", "x", lo_open=True),
    "eps_surprise_pct": Band(-500, 500, "EPS surprise", "%", scale=100),      # stored as a fraction
    "move_2d_pct": Band(-80, 300, "2-day move", "%", scale=100),             # stored as a fraction
    "daily_return": Band(-90, 900, "1-day return", "%", scale=100),           # stored as a fraction: [-0.9, 9]
    "yield_pct": Band(-5, 40, "Yield", "%"),                                  # percent
    "gdp_yoy_pct": Band(-30, 30, "GDP growth", "%", scale=100),              # year over year, stored as a fraction
}

_BLANK_NOTES = contextvars.ContextVar("blank_notes", default=None)


def num(x):
    """A finite float, or None (see the module docstring)."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, str):
        s = x.strip()
        if s.lower() in _EMPTY_TEXT:
            return None
        try:
            v = float(s)
        except ValueError:
            return None
    elif isinstance(x, numbers.Real):
        try:
            v = float(x)
        except (OverflowError, TypeError, ValueError):
            return None
    else:
        return None
    if not math.isfinite(v) or v in PLACEHOLDERS:
        return None
    return v


def finite(x):
    """x as a float when it is a finite number (bool excluded), else None. No placeholder or text handling."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        v = float(x)
    except OverflowError:
        return None
    return v if math.isfinite(v) else None


def in_band(metric: str, x) -> bool:
    b = BANDS[metric]
    v = num(x)
    if v is None:
        return False
    lo, hi = b.lo / b.scale, b.hi / b.scale        # limits in the stored unit: 300 / 100 == 3.0 exactly, 3.0 * 100 may not be
    return (lo < v if b.lo_open else lo <= v) and v <= hi


def _fmt(v: float) -> str:
    return f"{v:,.4g}" if abs(v) < 1e6 else f"{v:,.3e}"


def band_note(metric: str, x, what: str | None = None) -> str:
    b = BANDS[metric]
    v = num(x)
    shown = "a non-number" if v is None else _fmt(v * b.scale) + b.unit
    rng = (f"above {_fmt(b.lo)}{b.unit} up to {_fmt(b.hi)}{b.unit}" if b.lo_open
           else f"{_fmt(b.lo)}{b.unit} to {_fmt(b.hi)}{b.unit}")
    return f"{b.label}{' for ' + what if what else ''} left blank: {shown} is outside the plausible range ({rng})"


def banded(metric: str, x, what: str | None = None):
    """x as a float when num(x) lies inside BANDS[metric]; else None, with the reason noted on the answer.
    None in gives None out, silently (a missing figure is not an implausible one)."""
    v = num(x)
    if v is None:
        return None
    if in_band(metric, v):
        return v
    note_blank(band_note(metric, v, what))
    return None


def note_blank(text: str) -> None:
    """Record, for the request (or cache fetch) running now, that a figure was blanked and why."""
    notes = _BLANK_NOTES.get()
    if notes is not None and text not in notes:
        notes.append(text)


def nonfinite_path(obj, _path: str = "", _depth: int = 0):
    """Path of the first NaN/Inf float in obj (dicts, lists, tuples), or None."""
    if isinstance(obj, float):
        return None if math.isfinite(obj) else (_path or "(top)")
    if _depth > 64:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = nonfinite_path(v, f"{_path}.{k}" if _path else str(k), _depth + 1)
            if p:
                return p
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            p = nonfinite_path(v, f"{_path}[{i}]", _depth + 1)
            if p:
                return p
    return None
