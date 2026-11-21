"""Enterprise value must be currency-consistent when a company reports in another currency than it trades in."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "terminal"))
import composite as c  # noqa: E402

CHECKS = 0


def near(got, want, tol, what):
    global CHECKS
    CHECKS += 1
    assert got is not None and abs(got - want) <= tol, f"{what}: got {got}, want {want} +/- {tol}"


def is_none(got, what):
    global CHECKS
    CHECKS += 1
    assert got is None, f"{what}: got {got}, want None"


def main():
    # Novo Nordisk ADR: trades in USD, reports in DKK. Yahoo EV = 184.7 (USD) + 87.0 (DKK) = 271.7 "mixed".
    nvo = {"currency": "DKK", "market_cap": 184.7e9, "enterprise_value": 271.7e9, "enterprise_to_ebitda": 1.496,
           "enterprise_to_revenue": 0.85, "price_to_book": 9.0}
    e = c._ev_parts(nvo, "USD", {"USD": 1.0, "DKK": 0.155})
    near(e["mcap_usd"], 184.7, 0.01, "NVO market cap")
    near(e["ev_usd"], 198.185, 0.05, "NVO EV in USD")
    near(e["ev_ebitda"], 7.04, 0.05, "NVO EV/EBITDA")
    near(e["nd_ebitda"], 0.479, 0.005, "NVO net debt/EBITDA")
    near(e["ev_sales"], 198.185 / 0.155 / (271.7 / 0.85), 0.01, "NVO EV/Sales")
    is_none(e["pb"], "NVO P/B (book value in DKK)")
    near(e["ev_q"], 198.185e9, 0.05e9, "NVO EV in trading currency")

    # Shell in London: quoted in pence, market cap in GBP, reports in USD.
    minor = c.MINOR_UNITS["GBp"][1]
    shel = {"currency": "USD", "market_cap": 209.9e9, "enterprise_value": 253.8e9, "enterprise_to_ebitda": 3.757}
    e = c._ev_parts(shel, "GBp", {"GBp": 1.3 * minor, "USD": 1.0})
    near(e["mcap_usd"], 272.87, 0.1, "SHEL market cap in USD")
    near(e["ev_usd"], 316.77, 0.1, "SHEL EV in USD")
    near(e["ev_ebitda"], 4.69, 0.02, "SHEL EV/EBITDA")
    near(e["nd_ebitda"], 0.650, 0.005, "SHEL net debt/EBITDA")
    near(e["ev_q"], 316.77e9 / 1.3, 0.2e9, "SHEL EV in GBP")

    # Same currency: Yahoo's figures pass through unchanged.
    aapl = {"currency": "USD", "market_cap": 3000e9, "enterprise_value": 3050e9, "enterprise_to_ebitda": 22.0,
            "enterprise_to_revenue": 7.5, "price_to_book": 45.0}
    e = c._ev_parts(aapl, "USD", {"USD": 1.0})
    near(e["ev_usd"], 3050.0, 0.001, "AAPL EV")
    near(e["ev_ebitda"], 22.0, 1e-9, "AAPL EV/EBITDA")
    near(e["ev_sales"], 7.5, 1e-9, "AAPL EV/Sales")
    near(e["pb"], 45.0, 1e-9, "AAPL P/B")
    near(e["nd_ebitda"], 50 / (3050 / 22.0), 1e-6, "AAPL net debt/EBITDA")

    # Reporting currency known but no exchange rate: EV figures blank, never a mixed-currency number.
    e = c._ev_parts(nvo, "USD", {"USD": 1.0})
    is_none(e["ev_usd"], "no FX: EV")
    is_none(e["ev_ebitda"], "no FX: EV/EBITDA")
    near(e["mcap_usd"], 184.7, 0.01, "no FX: market cap still shown")

    # Reporting currency unknown and EV wildly off market cap (old ADR guard): blank.
    tsm = {"market_cap": 900e9, "enterprise_value": 28000e9, "enterprise_to_ebitda": 12.0, "price_to_book": 200.0}
    e = c._ev_parts(tsm, "USD", {"USD": 1.0})
    is_none(e["ev_usd"], "unknown currency guard: EV")
    is_none(e["pb"], "unknown currency guard: P/B")
    is_none(e["nd_ebitda"], "unknown currency guard: net debt/EBITDA")

    # Whole EV given in the reporting currency (TSM-style: market cap USD, EV in TWD). True: mcap 1000 USD = 32000 TWD,
    # net cash 2000 TWD, EV 30000 TWD = 937.5 USD, EBITDA 1500 TWD.
    tw = {"currency": "TWD", "market_cap": 1000e9, "enterprise_value": 30000e9, "enterprise_to_ebitda": 20.0, "price_to_book": 9.0}
    e = c._ev_parts(tw, "USD", {"USD": 1.0, "TWD": 1 / 32})
    near(e["ev_usd"], 937.5, 0.01, "whole-EV case: EV in USD")
    near(e["ev_ebitda"], 20.0, 1e-9, "whole-EV case: multiple unchanged")
    near(e["nd_ebitda"], -2000 / 1500, 1e-6, "whole-EV case: net cash / EBITDA")
    is_none(e["pb"], "whole-EV case: P/B")

    # Unusable Yahoo EV (seen live for TSM and ASML ADRs): neither reading is plausible, so everything EV-based is blank.
    bad1 = {"currency": "TWD", "market_cap": 1937e9, "enterprise_value": 12960e9, "enterprise_to_ebitda": 3.7}
    e = c._ev_parts(bad1, "USD", {"USD": 1.0, "TWD": 1 / 32})
    is_none(e["ev_usd"], "TSM-style unusable EV")
    is_none(e["nd_ebitda"], "TSM-style unusable net debt/EBITDA")
    bad2 = {"currency": "EUR", "market_cap": 542e9, "enterprise_value": 33000e9, "enterprise_to_ebitda": 1900.0}
    e = c._ev_parts(bad2, "USD", {"USD": 1.0, "EUR": 1.05})
    is_none(e["ev_usd"], "ASML-style unusable EV")
    is_none(e["ev_ebitda"], "ASML-style unusable multiple")

    # Net cash company reporting in another currency: EV below market cap, still consistent.
    cash = {"currency": "JPY", "market_cap": 100e9, "enterprise_value": 100e9 - 3000e9, "enterprise_to_ebitda": -2.0}
    e = c._ev_parts(cash, "USD", {"USD": 1.0, "JPY": 0.0065})
    near(e["ev_usd"], 100 - 19.5, 0.01, "net cash EV")
    is_none(e["ev_ebitda"], "negative Yahoo multiple gives no EBITDA")
    print(f"test_ev: {CHECKS} checks passed")


if __name__ == "__main__":
    main()
