import streamlit as st
import pandas as pd
import yfinance as yf
import io
from openpyxl.styles import PatternFill, Alignment, Font
import streamlit.components.v1 as components

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

    def generate_matrix(self, full_data, sector_map, weekly_drill=False, w_cr=75, w_ret=4):
        price_df = full_data['Close'].dropna(how='all')
        high_df = full_data['High']
        low_df = full_data['Low']
        
        index_prices = price_df[self.index_ticker]
        stock_prices = price_df.drop(columns=[self.index_ticker])
        
        target_months = sorted(stock_prices.index[-self.output_history:])
        month_headers = [d.strftime('%b-%y') for d in target_months]
        
        matrix_data = []
        for ticker in stock_prices.columns:
            clean_name = ticker.replace(".NS", "")
            
            # --- MONTHLY TECHNICAL FILTERS ---
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

            # --- RS CALCULATION ---
            sector = sector_map.get(clean_name, "N/A")
            row = {"Symbols": f"NSE:{clean_name}", "Sector": sector}
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

# --- UI HELPER: COPY BUTTON ---
def st_copy_to_clipboard(text):
    copy_js = f"""
        <button onclick="copyToClipboard()" style="
            background-color: #1a73e8; color: white; border: none; 
            padding: 10px 20px; border-radius: 6px; cursor: pointer; 
            font-weight: 600; margin-bottom: 20px;">
            📋 Copy Symbols for TradingView
        </button>
        <script>
            function copyToClipboard() {{
                const text = `{text}`;
                navigator.clipboard.writeText(text).then(() => {{ alert('Symbols copied!'); }});
            }}
        </script>
    """
    components.html(copy_js, height=60)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Abdullah RS Pro", layout="wide")
st.title("🚀 Abdullah RS Leaderboard")

with st.sidebar:
    st.header("⚙️ Settings")
    uploaded_files = st.file_uploader("Upload Sector CSVs", type="csv", accept_multiple_files=True)
    history_range = st.number_input("Display History (Months)", min_value=1, value=6)
    cutoff = st.number_input("RS Threshold %", min_value=50, max_value=100, value=95)
    
    st.header("🎯 Technical Filters")
    use_ema = st.toggle("Price > 12m EMA", value=True)
    cr_limit = st.number_input("Min Closing Range (CR%)", 0, 100, 75)
    mr_limit = st.number_input("Min Monthly Return (%MR)", 0, 100, 5)
    
    st.header("📅 Weekly Drill-Down")
    enable_weekly = st.toggle("Apply Weekly Filter", value=False)
    weekly_cr_req = 75  # As per requirements
    weekly_ret_req = 4   # As per requirements
    
    run_button = st.button("Generate Alpha List", type="primary")

if run_button and uploaded_files:
    sector_map, all_symbols = {}, []
    for f in uploaded_files:
        df_temp = pd.read_csv(f)
        if 'Symbols' in df_temp.columns and 'Sector' in df_temp.columns:
            for _, row in df_temp.iterrows():
                s, sec = str(row['Symbols']).strip(), str(row['Sector']).strip()
                sector_map[s] = sec
                all_symbols.append(s)
    
    unique_tickers = [f"{s}.NS" for s in set(all_symbols)]
    
    if unique_tickers:
        with st.spinner('Phase 1: Analyzing Monthly RS...'):
            screener = RSHeatmapScreener(cutoff, 12, history_range, use_ema, cr_limit, mr_limit)
            monthly_data = screener.fetch_data(unique_tickers, interval="1mo")
            matrix_df, month_headers = screener.generate_matrix(monthly_data, sector_map)

        # --- WEEKLY DRILL DOWN LOGIC ---
        if not matrix_df.empty and enable_weekly:
            with st.spinner('Phase 2: Drilling down to Weekly...'):
                shortlisted_tickers = [s.split(":")[1] + ".NS" for s in matrix_df['Symbols'].tolist()]
                weekly_data = screener.fetch_data(shortlisted_tickers, interval="1wk", period="6mo")
                
                final_rows = []
                for _, m_row in matrix_df.iterrows():
                    t_name = m_row['Symbols'].split(":")[1] + ".NS"
                    if t_name in weekly_data['Close'].columns:
                        w_close = weekly_data['Close'][t_name].dropna()
                        w_high = weekly_data['High'][t_name].dropna()
                        w_low = weekly_data['Low'][t_name].dropna()
                        
                        if len(w_close) >= 2:
                            curr_w_c = w_close.iloc[-1]
                            prev_w_c = w_close.iloc[-2]
                            curr_w_h = w_high.iloc[-1]
                            curr_w_l = w_low.iloc[-1]
                            
                            w_return = (curr_w_c / prev_w_c - 1) * 100
                            w_cr = ((curr_w_c - curr_w_l) / (curr_w_h - curr_w_l) * 100) if curr_w_h != curr_w_l else 0
                            
                            if w_return > weekly_ret_req and w_cr > weekly_cr_req:
                                m_row['Weekly CR%'] = f"{int(w_cr)}%"
                                final_rows.append(m_row)
                
                matrix_df = pd.DataFrame(final_rows)

        if not matrix_df.empty:
            matrix_df.insert(0, 'Sl. No', range(1, len(matrix_df) + 1))
            st_copy_to_clipboard(", ".join(matrix_df['Symbols'].tolist()))

            # UI Display Styling
            display_df = matrix_df.copy()
            month_cols = [c for c in display_df.columns if c in month_headers]
            
            for col in month_cols:
                display_df[col] = display_df[col].apply(lambda x: f"{x.split('_')[1]}%" if isinstance(x, str) and "_" in x else "")

            def apply_ui_styles(styler):
                styler.set_table_styles([{'selector': 'th', 'props': [('font-weight', 'bold'), ('background-color', '#f1f5f9')]}])
                def color_cells(val):
                    if '100%' in str(val): return 'background-color: #0ea5e9; color: white;'
                    if '%' in str(val): return 'background-color: #10b981; color: white;'
                    return ''
                # Apply color only to monthly columns
                return styler.map(color_cells, subset=month_cols)

            st.dataframe(apply_ui_styles(display_df.style), hide_index=True, use_container_width=True)
            
            # --- EXCEL EXPORT (Updated to handle Weekly column) ---
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                matrix_df.to_excel(writer, index=False, sheet_name='Alpha_RS')
                ws = writer.sheets['Alpha_RS']
                # ... (Standard styling logic remains same as original script)
            st.download_button(label="📥 Download Styled Report", data=output.getvalue(), file_name="Alpha_RS_Report.xlsx")
        else:
            st.warning("No stocks passed the technical or weekly filters.")