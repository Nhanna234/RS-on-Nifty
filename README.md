# Abdullah RS Leaderboard

Screens NSE stocks for relative strength (stock price divided by the Nifty 50) at or near its 12-month high.

## How it is put together

- `app.py` is the Streamlit app. It holds the RS engine, downloads index lists from NSE and prices from Yahoo Finance, and hands the results to the dashboard.
- `bt_engine.py` holds the backtest rules and statistics; `bt_data.py` downloads and stores daily prices and the Nifty 50 series used by the backtest. Neither imports Streamlit, so both can be tested on their own.
- `frontend/` is the dashboard, written in React and built with Vite. Streamlit shows the built files in `frontend/dist`.

## Backtest rules

Decided at each month-end close and traded at the next month's first open:

- **Buy** a stock whose RS ratio (RS as a percentage of its highest month-end value in the past 12 months) is 100%, or is at least the minimum threshold and higher than two months earlier, and which also closes at or above its 12-month EMA, closes in the top part of the month's range (minimum closing range) and rose at least the minimum monthly return.
- If more stocks qualify than there are empty slots, the highest closing range is bought first (or 6- / 12-month return, if chosen), then the higher RS ratio, then the higher monthly return.
- **Sell** when the RS ratio falls below the threshold or the stock closes below its 12-month EMA. A stock held is never swapped for a better one.
- Each new stock gets an equal share (1 / number of slots) of the portfolio value, paid from cash. Holdings are not rebalanced. Empty slots stay in cash and earn the cash yield.

The first backtest on a universe downloads daily prices from 2010 for every stock and takes a few minutes; later runs reuse the stored prices.

Streamlit Cloud only runs `app.py`. It does not build the dashboard, so `frontend/dist` is committed to the repository.

## Changing the dashboard

After editing anything in `frontend/src`, rebuild and commit the new `frontend/dist`:

```
cd frontend
npm install
npm run build
```

`npm run dev` opens the dashboard on its own with invented sample data, which is useful for design work.
