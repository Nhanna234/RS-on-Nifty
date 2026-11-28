# Working on this repository

This is Ali's RS Leaderboard app ("Abu Abdullah"): a Streamlit app (`app.py`) with a React + Vite dashboard (`frontend/`, built into `frontend/dist`, which must be committed because Streamlit Cloud does not build it).

## Rules document: keep it current

`RULES.md` is the written rulebook of the screener and the backtest, and the app's **Rules** tab displays it. Whenever you change any logic, filter, default or data handling (in `app.py`, `bt_engine.py`, `bt_data.py`, `lender_filter.py`, `compare.py`, `sweep.py`, `us_universe.py`, `price_snapshot.py`, `market_dash.py`, `sectors.py`, `rs_leaders.py` or `settings.py`):

1. Update the relevant section of `RULES.md` so it describes what the code now does.
2. Add a dated entry at the top of the **Change log** in `RULES.md`, in plain language.
3. Run `python rules_doc.py --write` to regenerate the Parameters section from the code, then `python rules_doc.py` to check it.

Adjustable values belong in `settings.py` (or a named constant in the module that uses them), so they appear in the generated Parameters section. A GitHub check (`.github/workflows/rules-check.yml`) fails if the Parameters section is out of date and warns when strategy code changes without `RULES.md` changing.

## Other notes

- `data/nasdaq100.csv` is the fallback Nasdaq-100 list used when Nasdaq and Wikipedia do not answer; refresh it (and `SNAPSHOT_DATE` in `us_universe.py`) after an index change.
- After changing `frontend/src`, run `cd frontend && npm install && npm run build` and commit `frontend/dist`.
- Sign-in: `auth.py` (password hashing, lockout, the "keep me signed in" cookie) and the sign-in pages in `app.py`. Accounts live in the app's Secrets under `[login]` (locally `.streamlit/secrets.toml`, which is git-ignored and must never be committed). With no accounts configured the site stays locked and shows a setup page. Sign-in is currently switched off (`LOGIN_REQUIRED = False` in `settings.py`); set it to `True` to bring it back.
- Saved prices: `price_snapshot.py` packs the price store into `prices_in.tar.gz` / `prices_us.tar.gz` on the GitHub release `price-store`. `.github/workflows/price-snapshot.yml` rebuilds them on weekdays at 20:00 and 08:00 IST (start it by hand with `gh workflow run price-snapshot.yml`). The app restores the file once when its price folder is empty (`_price_store` in `app.py`) and falls back to Yahoo if that fails. Locally: `python price_snapshot.py build --market IN --out prices_in.tar.gz`.
- `auth.py`, `bt_engine.py`, `bt_data.py`, `lender_filter.py`, `compare.py`, `sweep.py`, `us_universe.py`, `settings.py`, `price_snapshot.py`, `market_dash.py`, `sectors.py`, `rs_leaders.py` and `rules_doc.py` have no Streamlit imports and can be tested on their own.
- Market breadth and Sector rotation tabs (moved from the Abdullah Dashboard, github.com/Nhanna234/Abdullah_Dashboard, on 28 Nov 2026): `market_dash.py` (index trend states, regime, Nifty 500 breadth, the data sent to those tabs), `sectors.py` (sector rotation, Rank(1,3,6)) and `rs_leaders.py` (the RS screen of the leading indices' stocks). Their data loaders are cached in `app.py` (`_md_*`, `run_market`) and passed in, so these modules have no Streamlit imports. The front end is `frontend/src/dash/` (`Market.jsx` draws the pages inside a shadow root with their own style sheet `dash.css`, so the two apps' styles stay apart). Rules: RULES.md section 11.
- Ali is new to coding: explain changes in plain language.
