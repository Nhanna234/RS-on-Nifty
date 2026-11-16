import streamlit as st
import pandas as pd
import yfinance as yf
import io
import requests
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Alignment, Font
import streamlit.components.v1 as components

# --- STANDARD INDEX UNIVERSES ---
# Official NSE Indices constituent files (columns: Company Name, Industry, Symbol, Series, ISIN Code)
INDEX_FILES = {
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
CONSTITUENT_MIRRORS = [
    "https://niftyindices.com/IndexConstituent/{f}",
    "https://www.niftyindices.com/IndexConstituent/{f}",
    "https://nsearchives.nseindia.com/content/indices/{f}",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def clean_symbol(s):
    """'NSE:tcs', 'TCS.NS', ' tcs ' -> 'TCS'"""
    s = str(s).strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def _download_index_csv(fname):
    """Try each mirror in turn; return the raw constituent table."""
    errors = []
    for m in CONSTITUENT_MIRRORS:
        url = m.format(f=fname)
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=25)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [str(c).strip() for c in df.columns]
            if "Symbol" not in df.columns or len(df) == 0:
                raise ValueError("file has no 'Symbol' column or is empty")
            return df
        except Exception as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("\n".join(errors))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def load_index_constituents(index_name):
    """Returns (table with Symbol / Industry / ISIN Code, note on where it came from)."""
    try:
        df = _download_index_csv(INDEX_FILES[index_name])
        note = "official NSE Indices list"
    except Exception as first_error:
        # Two indices can be rebuilt exactly from other lists (NSE's own definitions)
        if index_name == "Nifty Total Market":      # = Nifty 500 + Nifty Microcap 250
            df = pd.concat([_download_index_csv(INDEX_FILES["Nifty 500"]),
                            _download_index_csv(INDEX_FILES["Nifty Microcap 250"])])
            note = "built from Nifty 500 + Nifty Microcap 250 lists"
        elif index_name == "Nifty Midcap 150":      # = Nifty LargeMidcap 250 minus Nifty 100
            lm = _download_index_csv(INDEX_FILES["Nifty LargeMidcap 250"])
            n100 = _download_index_csv(INDEX_FILES["Nifty 100"])
            df = lm[~lm["Symbol"].map(clean_symbol).isin(set(n100["Symbol"].map(clean_symbol)))]
            note = "built from Nifty LargeMidcap 250 minus Nifty 100 lists"
        else:
            raise RuntimeError(f"Could not download the {index_name} list.\n{first_error}")
    if "Series" in df.columns:
        df = df[df["Series"].astype(str).str.strip().isin(["EQ", "BE", "BZ"])]
    for c in ("Industry", "ISIN Code"):
        if c not in df.columns:
            df[c] = ""
    df = df.copy()
    df["Symbol"] = df["Symbol"].map(clean_symbol)
    return df.drop_duplicates("Symbol")[["Symbol", "Industry", "ISIN Code"]].reset_index(drop=True), note


def read_uploaded_universe(files):
    """Accepts the original format (Symbols, Sector, ISIN) and the NSE format (Symbol, Industry, ISIN Code)."""
    sector_map, symbols = {}, []
    for f in files:
        df_temp = pd.read_csv(f)
        df_temp.columns = [str(c).strip() for c in df_temp.columns]
        sym_col = next((c for c in ("Symbols", "Symbol") if c in df_temp.columns), None)
        if sym_col is None:
            continue
        sec_col = next((c for c in ("Sector", "Industry") if c in df_temp.columns), None)
        isin_col = next((c for c in ("ISIN", "ISIN Code") if c in df_temp.columns), None)
        for _, row in df_temp.iterrows():
            if pd.isna(row[sym_col]):
                continue
            s = clean_symbol(row[sym_col])
            if not s:
                continue
            sec = str(row[sec_col]).strip() if sec_col and pd.notna(row[sec_col]) else "N/A"
            isin_code = str(row[isin_col]).strip() if isin_col and pd.notna(row[isin_col]) else ""
            sector_map[s] = {"Sector": sec, "ISIN": isin_code}
            symbols.append(s)
    return sector_map, symbols


# --- RS LOGIC ENGINE ---
class RSHeatmapScreener:
    def __init__(self, rs_threshold, lookback_months, output_history, use_ema, cr_threshold, mr_threshold):
        self.rs_threshold = rs_threshold / 100.0
        self.lookback_months = lookback_months
        self.output_history = output_history
        self.index_ticker = "^NSEI"
        self.use_ema = use_ema
        self.cr_threshold = cr_threshold / 100.0
        self.mr_threshold = mr_threshold / 100.0

    def fetch_data(self, tickers, interval="1mo", period="3y"):
        all_tickers = tickers + [self.index_ticker]
        data = yf.download(all_tickers, period=period, interval=interval, auto_adjust=True)
        return data

    def generate_matrix(self, full_data, sector_map):
        price_df = full_data['Close'].dropna(how='all')
        index_prices = price_df[self.index_ticker]
        stock_prices = price_df.drop(columns=[self.index_ticker])
        high_df = full_data['High']
        low_df = full_data['Low']
        
        target_months = sorted(stock_prices.index[-self.output_history:])
        month_headers = [d.strftime('%b-%y') for d in target_months]
        
        matrix_data = []
        for ticker in stock_prices.columns:
            clean_name = ticker.replace(".NS", "")
            
            # MONTHLY FILTERS
            curr_price = stock_prices[ticker].iloc[-1]
            prev_price = stock_prices[ticker].iloc[-2]
            curr_high = high_df[ticker].iloc[-1]
            curr_low = low_df[ticker].iloc[-1]
            
            if self.use_ema:
                ema_12 = stock_prices[ticker].ewm(span=12, adjust=False).mean().iloc[-1]
                if curr_price < ema_12: continue
            
            cr_val = (curr_price - curr_low) / (curr_high - curr_low) if curr_high != curr_low else 0
            if (cr_val * 100) < self.cr_threshold: continue
            
            mr_val = (curr_price / prev_price) - 1
            if (mr_val * 100) < self.mr_threshold: continue

            # Extract mapping details safely
            sector = sector_map.get(clean_name, {}).get("Sector", "N/A")
            isin_val = sector_map.get(clean_name, {}).get("ISIN", "")

            row = {"Symbols": f"NSE:{clean_name}", "Sector": sector, "ISIN_Hidden": isin_val}
            has_any_dot = False
            full_rs_series = (stock_prices[ticker] / index_prices).dropna()

            for i, date in enumerate(target_months):
                header = month_headers[i]
                if date in full_rs_series.index:
                    idx = full_rs_series.index.get_loc(date)
                    if idx >= self.lookback_months:
                        window = full_rs_series.iloc[idx - self.lookback_months + 1 : idx + 1]
                        max_rs = window.max()
                        current_rs = full_rs_series.loc[date]
                        retainment = (current_rs / max_rs)
                        
                        if retainment >= 1.0:
                            row[header] = f"CYAN_{100}"
                            has_any_dot = True
                        elif retainment >= self.rs_threshold:
                            row[header] = f"GREEN_{int(retainment * 100)}"
                            has_any_dot = True
            
            if has_any_dot:
                matrix_data.append(row)
        
        return pd.DataFrame(matrix_data), month_headers

# --- UI HELPER ---
def st_copy_to_clipboard(text):
    copy_js = f'<button onclick="copyToClipboard()" style="background-color: #1a73e8; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600; margin-bottom: 20px;">📋 Copy Symbols for TradingView</button><script>function copyToClipboard() {{const text = `{text}`;navigator.clipboard.writeText(text).then(() => {{ alert("Symbols copied!"); }});}}</script>'
    components.html(copy_js, height=60)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Abdullah RS Pro", layout="wide")
st.title("📈 Abdullah RS Leaderboard")

with st.sidebar:
    st.header("⚙️ Settings")
    index_name = st.selectbox("Stock Universe (Index)", list(INDEX_FILES),
                              index=list(INDEX_FILES).index(DEFAULT_INDEX))
    uploaded_files = st.file_uploader(
        "Optional: upload your own CSV(s)", type="csv", accept_multiple_files=True,
        help="If a file is uploaded, its stocks are used INSTEAD of the index selected above. "
             "Columns: Symbols, Sector, ISIN (or the NSE format: Symbol, Industry, ISIN Code).")
    if uploaded_files:
        st.caption("Uploaded file is in use; the index selection above is ignored.")
    history_range = st.number_input("Display History (Months)", min_value=1, value=6)
    cutoff = st.number_input("RS Threshold %", min_value=50, max_value=100, value=100)
    
    st.header("🔍 Technical Filters")
    use_ema = st.toggle("Price > 12m EMA", value=True)
    cr_limit = st.number_input("Min Closing Range (CR%)", 0, 100, 75)
    mr_limit = st.number_input("Min Monthly Return (%MR)", 0, 100, 5)
    
    st.header("🗓️ Weekly Drill-Down")
    enable_weekly = st.toggle("Apply Weekly Filter", value=False)
    weekly_cr_req = 75
    weekly_ret_req = 4
    
    run_button = st.button("Generate Alpha List", type="primary")

if run_button:
    sector_map, all_symbols = {}, []
    universe_label = ""
    if uploaded_files:
        # Uploaded stocks supersede the index list
        sector_map, all_symbols = read_uploaded_universe(uploaded_files)
        universe_label = "uploaded CSV"
        if not all_symbols:
            st.error("The uploaded file has no 'Symbols' (or 'Symbol') column, or it is empty. "
                     "Fix the file, or remove it to run on the selected index.")
    else:
        try:
            with st.spinner(f"Fetching {index_name} constituents..."):
                const_df, source_note = load_index_constituents(index_name)
            for _, row in const_df.iterrows():
                s = row["Symbol"]
                sec = str(row["Industry"]).strip() if pd.notna(row["Industry"]) and str(row["Industry"]).strip() else "N/A"
                isin_code = str(row["ISIN Code"]).strip() if pd.notna(row["ISIN Code"]) else ""
                sector_map[s] = {"Sector": sec, "ISIN": isin_code}
                all_symbols.append(s)
            universe_label = f"{index_name} ({source_note})"
        except Exception as e:
            st.error(f"Could not load the {index_name} stock list from NSE. "
                     f"Try again, or upload a CSV instead.\n\n{e}")

    unique_tickers = [f"{s}.NS" for s in sorted(set(all_symbols))]

    if unique_tickers:
        st.caption(f"Universe: {universe_label} | {len(unique_tickers)} stocks")
        with st.spinner('Phase 1: Analyzing Monthly RS...'):
            screener = RSHeatmapScreener(cutoff, 12, history_range, use_ema, cr_limit, mr_limit)
            monthly_data = screener.fetch_data(unique_tickers, interval="1mo")
            matrix_df, month_headers = screener.generate_matrix(monthly_data, sector_map)

        if not matrix_df.empty:
            if enable_weekly:
                with st.spinner('Phase 2: Drilling down to Weekly & Daily metrics...'):
                    shortlisted_tickers = [s.split(":")[1] + ".NS" for s in matrix_df['Symbols'].tolist()]
                    weekly_data = screener.fetch_data(shortlisted_tickers, interval="1wk", period="6mo")
                    daily_data = screener.fetch_data(shortlisted_tickers, interval="1d", period="1mo")
                    
                    final_rows = []
                    for _, m_row in matrix_df.iterrows():
                        t_name = m_row['Symbols'].split(":")[1] + ".NS"
                        if t_name in weekly_data['Close'].columns:
                            w_close, w_high, w_low = weekly_data['Close'][t_name].dropna(), weekly_data['High'][t_name].dropna(), weekly_data['Low'][t_name].dropna()
                            if len(w_close) >= 2:
                                w_return = (w_close.iloc[-1] / w_close.iloc[-2] - 1) * 100
                                w_cr = ((w_close.iloc[-1] - w_low.iloc[-1]) / (w_high.iloc[-1] - w_low.iloc[-1]) * 100) if w_high.iloc[-1] != w_low.iloc[-1] else 0
                                
                                if w_return > weekly_ret_req and w_cr > weekly_cr_req:
                                    m_row['Weekly CR%'] = f"{int(w_cr)}%"
                                    
                                    if t_name in daily_data['Close'].columns:
                                        d_close = daily_data['Close'][t_name].dropna()
                                        d_high = daily_data['High'][t_name].dropna()
                                        d_low = daily_data['Low'][t_name].dropna()
                                        
                                        if len(d_close) >= 2:
                                            d_cr = ((d_close.iloc[-1] - d_low.iloc[-1]) / (d_high.iloc[-1] - d_low.iloc[-1]) * 100) if d_high.iloc[-1] != d_low.iloc[-1] else 0
                                            d_change = (d_close.iloc[-1] / d_close.iloc[-2] - 1) * 100
                                            
                                            m_row['Daily CR%'] = f"{int(d_cr)}%"
                                            m_row['Daily % Change'] = f"{d_change:.2f}%"
                                        else:
                                            m_row['Daily CR%'] = ""
                                            m_row['Daily % Change'] = ""
                                    else:
                                        m_row['Daily CR%'] = ""
                                        m_row['Daily % Change'] = ""
                                        
                                    final_rows.append(m_row)
                    matrix_df = pd.DataFrame(final_rows)

            if not matrix_df.empty:
                # --- SORTING BY SECTOR THEN SYMBOLS ---
                matrix_df = matrix_df.sort_values(by=["Sector", "Symbols"]).reset_index(drop=True)
                
                # Extract identifiers
                symbols_list = matrix_df['Symbols'].tolist()
                isin_list = [str(i).strip() for i in matrix_df['ISIN_Hidden'].tolist() if pd.notna(i) and str(i).strip() != ""]
                
                # Render action area layout
                col_btn1, col_btn2 = st.columns([1, 4])
                with col_btn1:
                    st_copy_to_clipboard(", ".join(symbols_list))
                with col_btn2:
                    isin_df = pd.DataFrame({"ISIN": isin_list})
                    # header=False completely removes the "ISIN" column name row
                    isin_csv = isin_df.to_csv(index=False, header=False).encode('utf-8')
                    st.download_button(
                        label="💳 Download ISIN CSV",
                        data=isin_csv,
                        file_name="Shortlisted_ISINs.csv",
                        mime="text/csv"
                    )

                # Build clean display table
                base_cols = ["Symbols", "Sector"]
                available_months = [m for m in month_headers if m in matrix_df.columns]
                
                additional_cols = []
                if "Weekly CR%" in matrix_df.columns:
                    additional_cols.append("Weekly CR%")
                if "Daily CR%" in matrix_df.columns:
                    additional_cols.append("Daily CR%")
                if "Daily % Change" in matrix_df.columns:
                    additional_cols.append("Daily % Change")
                    
                final_cols_to_keep = base_cols + available_months + additional_cols
                export_df = matrix_df[final_cols_to_keep].copy()
                export_df.insert(0, 'Sl. No', range(1, len(export_df) + 1))

                # UI Display Clean Up
                display_df = export_df.copy()
                for col in available_months:
                    display_df[col] = display_df[col].apply(lambda x: f"{x.split('_')[1]}%" if isinstance(x, str) and "_" in x else "")

                def apply_ui_styles(styler):
                    styler.set_table_styles([{'selector': 'th', 'props': [('font-weight', 'bold'), ('background-color', '#f1f5f9')]}])
                    def color_cells(val):
                        if '100%' in str(val): return 'background-color: #0ea5e9; color: white;'
                        if '%' in str(val) and val != "": return 'background-color: #10b981; color: white;'
                        return ''
                    return styler.map(color_cells, subset=available_months)

                st.dataframe(apply_ui_styles(display_df.style), hide_index=True, use_container_width=True)
                
                # Excel Export
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    export_df.to_excel(writer, index=False, sheet_name='Alpha_RS')
                    ws = writer.sheets['Alpha_RS']
                    
                    blue_fill = PatternFill(start_color="0EA5E9", fill_type="solid")
                    green_fill = PatternFill(start_color="10B981", fill_type="solid")
                    header_fill = PatternFill(start_color="0F172A", fill_type="solid")
                    
                    for cell in ws[1]:
                        cell.fill, cell.font, cell.alignment = header_fill, Font(color="FFFFFF", bold=True), Alignment(horizontal='center')

                    for row in ws.iter_rows(min_row=2):
                        for cell in row:
                            cell.alignment = Alignment(horizontal='center')
                            val = str(cell.value) if cell.value else ""
                            if "_" in val:
                                tag, num = val.split("_")
                                cell.value = int(num) / 100.0
                                cell.number_format = '0%'
                                cell.font = Font(color="FFFFFF", bold=True)
                                cell.fill = blue_fill if tag == "CYAN" else green_fill

                st.download_button(label="📋 Download Styled Report", data=output.getvalue(), file_name="Alpha_RS_Report.xlsx")
        else:
            st.warning("No stocks passed the filters.")