# Working on this repository

This is Ali's RS Leaderboard app ("Abu Abdullah"): a Streamlit app (`app.py`) with a React + Vite dashboard (`frontend/`, built into `frontend/dist`, which must be committed because Streamlit Cloud does not build it).

## Rules document: keep it current

`RULES.md` is the written rulebook of the screener and the backtest, and the app's **Rules** tab displays it. Whenever you change any logic, filter, default or data handling (in `app.py`, `bt_engine.py`, `bt_data.py`, `lender_filter.py`, `compare.py` or `settings.py`):

1. Update the relevant section of `RULES.md` so it describes what the code now does.
2. Add a dated entry at the top of the **Change log** in `RULES.md`, in plain language.
3. Run `python rules_doc.py --write` to regenerate the Parameters section from the code, then `python rules_doc.py` to check it.

Adjustable values belong in `settings.py` (or a named constant in the module that uses them), so they appear in the generated Parameters section. A GitHub check (`.github/workflows/rules-check.yml`) fails if the Parameters section is out of date and warns when strategy code changes without `RULES.md` changing.

## Other notes

- After changing `frontend/src`, run `cd frontend && npm install && npm run build` and commit `frontend/dist`.
- `bt_engine.py`, `bt_data.py`, `lender_filter.py`, `compare.py`, `settings.py` and `rules_doc.py` have no Streamlit imports and can be tested on their own.
- Ali is new to coding: explain changes in plain language.
