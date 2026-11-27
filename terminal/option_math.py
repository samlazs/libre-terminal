"""Implied volatility and Greeks from a delayed option chain, for when Cboe's own chain feed is refused.

Yahoo's published IV cannot be passed through: it is a placeholder without live quotes (SPX showed 0.008 against about
0.31, 2026-11-21), and its underlying price is the after-hours one while the option quotes are from the close. So:
  * time to expiry runs from the quote time, not from now (on a Saturday, Monday's expiry has 3 days, not 1.7);
  * the forward of each expiry comes from put-call parity near the money, so dividends and carry need no guess;
  * each strike gets one Black-76 IV, from its out-of-the-money contract; no usable price means blank, never zero.
Units match Cboe: IV is a fraction, theta per calendar day, vega and rho per one percentage point.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import ndtr

MIN_IV, MAX_IV = 1e-4, 5.0  # openbb_cboe reads an IV of 5 or more as none
MIN_QUOTED = 0.2            # below this share of two-sided quotes the market is closed: use last trades instead
TRADE_WINDOW = 3600         # seconds from the quote time within which a last trade still counts
PARITY_STRIKES = 5
AM_SETTLED = {"SPX", "NDX", "RUT", "VIX", "VIXW"}       # roots that settle at the open of the expiration day
CALCULATED = ("implied_volatility", "theoretical_price", "delta", "gamma", "theta", "vega", "rho")


def black76(forward, strike, years, sigma, discount, is_call):
    """European option value on a forward."""
    dev = np.asarray(sigma, dtype=float) * np.sqrt(np.maximum(years, 0.0))
    intrinsic = np.where(is_call, discount * np.maximum(forward - strike, 0.0),
                         discount * np.maximum(strike - forward, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(forward / strike) + 0.5 * dev * dev) / dev
        d2 = d1 - dev
        val = np.where(is_call, discount * (forward * ndtr(d1) - strike * ndtr(d2)),
                       discount * (strike * ndtr(-d2) - forward * ndtr(-d1)))
    return np.where(dev > 0, val, intrinsic)


def implied_vol(price, forward, strike, years, discount, is_call):
    """The volatility each price implies; NaN where none between MIN_IV and MAX_IV does."""
    price = np.asarray(price, dtype=float)
    lo, hi = np.full(price.shape, MIN_IV), np.full(price.shape, MAX_IV)
    solvable = ((price > black76(forward, strike, years, lo, discount, is_call))
                & (price < black76(forward, strike, years, hi, discount, is_call)))
    for _ in range(50):
        mid = (lo + hi) / 2
        low = black76(forward, strike, years, mid, discount, is_call) < price
        lo, hi = np.where(low, mid, lo), np.where(low, hi, mid)
    return np.where(solvable, (lo + hi) / 2, np.nan)


def greeks(spot, forward, strike, years, sigma, rate, is_call) -> dict:
    """Greeks against spot. Theta holds the forward still: the carry of a few days' parity forward is mostly noise."""
    discount, root_t = np.exp(-rate * years), np.sqrt(years)
    d1 = (np.log(forward / strike) + 0.5 * sigma * sigma * years) / (sigma * root_t)
    d2 = d1 - sigma * root_t
    density = np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi)
    value = black76(forward, strike, years, sigma, discount, is_call)
    ratio = discount * forward / spot
    return {
        "theoretical_price": value,
        "delta": np.where(is_call, ratio * ndtr(d1), -ratio * ndtr(-d1)),
        "gamma": ratio * density / (spot * sigma * root_t),
        "theta": (-discount * forward * density * sigma / (2 * root_t) + rate * value) / 365,
        "vega": discount * forward * density * root_t / 100,
        "rho": np.where(is_call, ndtr(d2), -ndtr(-d2)) * strike * years * discount / 100,
    }


def parity_forward(strikes, calls, puts, discount) -> float:
    """Median of K + (C - P) / D over the strikes where the call and the put are closest; NaN without two pairs."""
    gap = np.asarray(calls, dtype=float) - np.asarray(puts, dtype=float)
    paired = ~np.isnan(gap)
    if paired.sum() < 2:
        return float("nan")
    k, gap = np.asarray(strikes, dtype=float)[paired], gap[paired]
    near = np.argsort(np.abs(gap), kind="stable")[:PARITY_STRIKES]
    return float(np.median(k[near] + gap[near] / discount))


def years_to_expiry(expiration, root: str, quoted_at, session_closed: bool = False) -> float:
    """Calendar years from the quote time to settlement (09:30 ET for AM-settled roots, else 16:00 ET).
    When session_closed is True, contracts expiring on or before the quote's New York session date have settled (0.0)."""
    ts = pd.Timestamp(quoted_at)
    quoted_et = ts.tz_convert("America/New_York") if ts.tzinfo else ts.tz_localize("America/New_York")
    if session_closed and expiration <= quoted_et.date():
        return 0.0
    clock = "09:30" if root in AM_SETTLED else "16:00"
    end = pd.Timestamp(f"{expiration} {clock}", tz="America/New_York")
    return (end - quoted_et).total_seconds() / (365 * 86400)


def analyse_chain(rows: pd.DataFrame, spot: float, quoted_at, rate: float, today, session_closed: bool = False) -> tuple[pd.DataFrame, str]:
    """(chain with calculated IV and Greeks, "mid" or "last" for the prices used).

    rows: contract_symbol, expiration (date), strike, option_type, bid, ask, last_trade_price, last_trade_time (zoned),
    open_interest, volume. Expired contracts are dropped. `main` marks one root per expiry (SPX or SPXW, never both
    on one curve; adjusted contracts lose): the others keep their open interest and volume, and get no IV.
    """
    df = rows[rows["expiration"] >= today].copy()
    df["root"] = df["contract_symbol"].str[:-15]
    left = {key: years_to_expiry(key[0], key[1], quoted_at, session_closed=session_closed) for key in set(zip(df["expiration"], df["root"]))}
    df = df[[left[key] > 0 for key in zip(df["expiration"], df["root"])]].reset_index(drop=True)
    df["dte"] = [(day - today).days for day in df["expiration"]]
    df["main"] = False
    for col in CALCULATED:
        df[col] = np.nan
    two_sided = (df["bid"] > 0) & (df["ask"] >= df["bid"])
    basis = "mid" if len(df) and two_sided.mean() >= MIN_QUOTED else "last"
    if basis == "mid":
        df["price"] = ((df["bid"] + df["ask"]) / 2).where(two_sided)
    else:
        age = (quoted_at - pd.to_datetime(df["last_trade_time"], utc=True)).dt.total_seconds().abs()
        df["price"] = df["last_trade_price"].where((df["last_trade_price"] > 0) & (age <= TRADE_WINDOW))

    for expiry, group in df.groupby("expiration"):
        best = None
        for root, part in group.groupby("root"):
            years = left[(expiry, root)]
            discount = float(np.exp(-rate * years))
            wide = (part.pivot_table(index="strike", columns="option_type", values="price", aggfunc="first")
                    .reindex(columns=["call", "put"]))
            strikes = wide.index.to_numpy(dtype=float)
            fwd, curve = parity_forward(strikes, wide["call"], wide["put"], discount), {}
            if fwd > 0:
                otm_call = strikes >= fwd
                solved = implied_vol(np.where(otm_call, wide["call"], wide["put"]), fwd, strikes, years, discount, otm_call)
                curve = {k: v for k, v in zip(strikes, solved) if not np.isnan(v)}
            score = (len(curve), len(part))
            if best is None or score > best[0]:
                best = (score, part, years, fwd, curve)
        _, part, years, fwd, curve = best
        df.loc[part.index, "main"] = True
        if not curve:
            continue
        sigma = part["strike"].map(curve)
        idx = part.index[sigma.notna().to_numpy()]
        sig = sigma.loc[idx].to_numpy(dtype=float)
        calc = greeks(spot, fwd, df.loc[idx, "strike"].to_numpy(dtype=float), years, sig, rate,
                      (df.loc[idx, "option_type"] == "call").to_numpy())
        df.loc[idx, "implied_volatility"] = sig
        for col, values in calc.items():
            df.loc[idx, col] = values

    # strategies need live quotes: with a handful of them the "nearest quoted strike" lands far from the one asked
    # (an SPX 5% strangle came out 6675 / 8005 on spot 6910), so they are blank then, as is an expiry without an
    # out-of-the-money ask on both sides of spot
    is_call = df["option_type"] == "call"
    otm = (is_call & (df["strike"] >= spot)) | (~is_call & (df["strike"] <= spot))
    asked = df[(df["ask"] > 0) & df["main"] & otm].groupby("expiration")["option_type"].nunique()
    priceable = (basis == "mid") & df["expiration"].isin(asked[asked == 2].index) & (df["ask"] > 0)
    df["bid"], df["ask"] = df["bid"].where(priceable), df["ask"].where(priceable)

    df["underlying_price"] = float(spot)
    sign = np.where(df["option_type"] == "call", 1, -1)
    contracts = df["open_interest"].fillna(0) * 100
    df["DEX"] = (df["delta"] * contracts * spot).fillna(0).astype("int64")          # whole dollars, as OpenBB's own
    df["GEX"] = (df["gamma"] * contracts * spot * spot * 0.01 * sign).fillna(0).astype("int64")
    df["Breakeven"] = df["strike"] + sign * df["ask"]
    df = df.drop(columns=["price", "root"]).sort_values(["expiration", "strike", "option_type"], kind="stable")
    return df.reset_index(drop=True), basis


def priced_part(df: pd.DataFrame, spot: float) -> pd.DataFrame:
    """The rows a volatility chart can plot: priced strikes of expiries with at least two priced strikes on each side of spot."""
    out = df[df["main"] & df["implied_volatility"].notna()]
    if out.empty:
        return out.reset_index(drop=True)
    uniq = out.drop_duplicates(["expiration", "strike"])
    below = uniq[uniq["strike"] < spot].groupby("expiration")["strike"].count()
    above = uniq[uniq["strike"] > spot].groupby("expiration")["strike"].count()
    keep = below[below >= 2].index.intersection(above[above >= 2].index)
    return out[out["expiration"].isin(keep)].reset_index(drop=True)
