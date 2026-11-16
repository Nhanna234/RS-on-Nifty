# Abdullah RS Leaderboard

Screens NSE stocks for relative strength (stock price divided by the Nifty 50) at or near its 12-month high.

## How it is put together

- `app.py` is the Streamlit app. It holds the RS engine, downloads index lists from NSE and prices from Yahoo Finance, and hands the results to the dashboard.
- `frontend/` is the dashboard, written in React and built with Vite. Streamlit shows the built files in `frontend/dist`.

Streamlit Cloud only runs `app.py`. It does not build the dashboard, so `frontend/dist` is committed to the repository.

## Changing the dashboard

After editing anything in `frontend/src`, rebuild and commit the new `frontend/dist`:

```
cd frontend
npm install
npm run build
```

`npm run dev` opens the dashboard on its own with invented sample data, which is useful for design work.
