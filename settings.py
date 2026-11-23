"""
Every adjustable number of the screener, the backtest and the comparison, in one place.

RULES.md shows these values in its "Parameters" section, which is generated from this file and the
constants in bt_engine.py, bt_data.py and lender_filter.py (see rules_doc.py). Change a value here,
then run `python rules_doc.py --write` and describe the change in RULES.md.
"""

# ---- universes
INDEX_FILES = {                      # NSE Indices constituent files
    "Nifty 50": "ind_nifty50list.csv",
    "Nifty 100": "ind_nifty100list.csv",
    "Nifty 200": "ind_nifty200list.csv",
    "Nifty LargeMidcap 250": "ind_niftylargemidcap250list.csv",
    "Nifty Midcap 150": "ind_niftymidcap150list.csv",
    "Nifty Smallcap 100": "ind_niftysmallcap100list.csv",
    "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
    "Nifty Microcap 250": "ind_niftymicrocap250_list.csv",
    "Nifty 500": "ind_nifty500list.csv",
    "Nifty Total Market": "ind_niftytotalmarket_list.csv",
}
DEFAULT_INDEX = "Nifty Total Market"
US_INDICES = ["Nasdaq 100"]          # US universes; their lists come from us_universe.py

# ---- markets: what changes between an Indian and a US universe
# The market follows the selected index; an uploaded CSV is read as the market of the index selected with it.
MARKETS = {
    "IN": {"exchange": "NSE", "yahoo_suffix": ".NS", "rs_index": "^NSEI", "rs_index_name": "Nifty 50",
           "currency": "INR", "capital": 1_000_000},
    "US": {"exchange": "NASDAQ", "yahoo_suffix": "", "rs_index": "^GSPC", "rs_index_name": "S&P 500",
           "currency": "USD", "capital": 10_000},
}


def market_of(index_name):
    """'US' for a US index, otherwise 'IN'."""
    return "US" if index_name in US_INDICES else "IN"


# ---- screener
SCREENER_DEFAULTS = {"index": DEFAULT_INDEX, "history": 6, "threshold": 100, "useEma": True, "cr": 75, "mr": 5,
                     "weekly": False, "excludeLenders": True}
LOOKBACK_MONTHS = 12          # RS is compared with its highest month-end value of the past 12 months
SCREENER_PRICE_PERIOD = "3y"  # monthly prices downloaded for the screener
RS_TRAIL_MONTHS = 24          # length of the RS line drawn for each stock
WEEKLY_RET_REQ = 4            # weekly drill-down: latest week's return must be above this %
WEEKLY_CR_REQ = 75            # weekly drill-down: latest week's closing range must be above this %
RS_INDEX_TICKER = MARKETS["IN"]["rs_index"]  # Nifty 50 price index on Yahoo: the denominator of relative strength

# ---- backtest
BT_DEFAULTS = {"index": DEFAULT_INDEX, "start": "2015-01", "slots": 10, "threshold": 98, "useEma": True,
               "cr": 75, "mr": 5, "cost": 0.2, "cashYield": 6, "capital": 1000000, "ranking": "cr",
               "excludeLenders": True}

# ---- all-index comparison
COMPARE_ORDER = ["Nifty Total Market", "Nifty 500", "Nifty 50", "Nifty 100", "Nifty 200", "Nifty LargeMidcap 250",
                 "Nifty Midcap 150", "Nifty Smallcap 100", "Nifty Smallcap 250", "Nifty Microcap 250"]
LONG_START = "2015-01"        # the comparison's second, long period starts here
