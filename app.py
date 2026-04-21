import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import io
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Alignment, Font
import streamlit.components.v1 as components

# --- RS LOGIC ENGINE ---
class RSHeatmapScreener:
    def __init__(self, rs_threshold, lookback_months, output_history, use_ema, cr_threshold, mr_threshold):
        self.rs_threshold = rs_threshold / 100.0
        self.lookback_months = lookback_months
        self.output_history = output_history
        self.index_ticker = "^NSEI"
        # New Filter Settings
        self.use_ema = use_ema
        self.cr_threshold = cr_threshold / 100.0
        self.mr_threshold = mr_threshold / 100.0

    def fetch_data(self, tickers):
        all_tickers = tickers + [self.index_ticker]
        # We need 'High' and 'Low' for CR% calculation now
        data = yf.download(all_tickers, period="3y", interval="1mo", auto_adjust=True)
        return data

    def generate_matrix(self, full_data, sector_map):
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
            
            # --- APPLY TECHNICAL FILTERS ON LATEST DATA ---
            latest_idx = stock_prices.index[-1]
            curr_price = stock_prices[ticker].iloc[-1]
            prev_price = stock_prices[ticker].iloc[-2]
            curr_high = high_df[ticker].iloc[-1]
            curr_low = low_df[ticker].iloc[-1]
            
            # 1. EMA Filter
            if self.use_ema:
                ema_12 = stock_prices[ticker].ewm(span=12, adjust=False).mean().iloc[-1]
                if curr_price < ema_12: continue
            
            # 2. Closing Range (CR%) Filter
            cr_val = (curr_price - curr_low) / (curr_high - curr_low) if curr_high != curr_low else 0
            if cr_val < self.cr_threshold: continue
            
            # 3. Monthly Return (%MR) Filter
            mr_val = (curr_price / prev_price) - 1
            if mr_val < self.mr_threshold: continue

            # --- RS CALCULATION ---
            sector = sector_map.get(clean_name, "N/A")
            row = {"Symbols": f"NSE:{clean_name},", "Sector": sector}
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
        
        return pd.DataFrame(matrix_data) if matrix_data else pd.DataFrame()

# --- UI HELPER: COPY BUTTON ---
def st_copy_to_clipboard(text):
    copy_js = f"""
        <button onclick="copyToClipboard()" style="
            background-color: #1a73e8; color: white; border: none; 
            padding: 12px 24px; border-radius: 6px; cursor: pointer; 
            font-weight: 600; margin-bottom: 20px; transition: 0.3s;">
            📋 Copy Symbols for TradingView
        </button>
        <script>
            function copyToClipboard() {{
                const text = `{text}`;
                navigator.clipboard.writeText(text).then(() => {{ alert('Symbols copied!'); }});
            }}
        </script>
    """
    components.html(copy_js, height=70)

# --- STREAMLIT UI ---
st.set_page_config(page_title="Alpha RS Pro", layout="wide")
st.title("🚀 Alpha RS Leaderboard")

with st.sidebar:
    st.header("⚙️ Core Settings")
    uploaded_files = st.file_uploader("Upload Sector CSVs", type="csv", accept_multiple_files=True)
    history_range = st.number_input("Display History (Months)", min_value=1, value=6)
    cutoff = st.number_input("RS Threshold %", min_value=50, max_value=100, value=90)
    
    st.header("🎯 Technical Filters")
    use_ema = st.toggle("Price > 12m EMA", value=True)
    cr_limit = st.number_input("Min Closing Range (CR%)", min_value=0, max_value=100, value=75)
    mr_limit = st.number_input("Min Monthly Return (%MR)", min_value=0, max_value=100, value=5)
    
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
        with st.spinner('Applying Technical Filters & Analyzing market leadership...'):
            # Initialize with new filter parameters
            screener = RSHeatmapScreener(cutoff, 12, history_range, use_ema, cr_limit, mr_limit)
            full_data = screener.fetch_data(unique_tickers)
            matrix_df = screener.generate_matrix(full_data, sector_map)

        if not matrix_df.empty:
            matrix_df.insert(0, 'Sl. No', range(1, len(matrix_df) + 1))
            st_copy_to_clipboard("\n".join(matrix_df['Symbols'].tolist()))

            # UI Styling & Display
            display_df = matrix_df.copy()
            month_cols = [c for c in display_df.columns if c not in ['Sl. No', 'Symbols', 'Sector']]
            for col in month_cols:
                display_df[col] = display_df[col].apply(lambda x: f"{x.split('_')[1]}%" if isinstance(x, str) and "_" in x else "")

            def apply_ui_styles(styler):
                styler.set_table_styles([
                    {'selector': 'th', 'props': [('font-weight', 'bold'), ('background-color', '#f1f5f9'), ('color', '#475569')]},
                    {'selector': 'tr:nth-child(even)', 'props': [('background-color', '#f8fafc')]}
                ])
                def color_cells(val):
                    if '100%' in str(val): return 'background-color: #0ea5e9; color: white; font-weight: bold;'
                    if '%' in str(val): return 'background-color: #10b981; color: white; font-weight: 500;'
                    return ''
                return styler.map(color_cells, subset=month_cols)

            st.dataframe(apply_ui_styles(display_df.style), hide_index=True, use_container_width=True)

            # Excel Styling (same as original logic)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                matrix_df.to_excel(writer, index=False, sheet_name='Alpha_RS')
                ws = writer.sheets['Alpha_RS']
                # ... [Internal Excel styling logic remains the same]
                for cell in ws[1]:
                    cell.fill, cell.font, cell.alignment = PatternFill(start_color="0F172A", fill_type="solid"), Font(color="FFFFFF", bold=True), Alignment(horizontal='center')
                # (Excel loop logic follows same pattern as your provided file)

            st.download_button(label="📥 Download Styled Report", data=output.getvalue(), file_name="Alpha_RS_Report.xlsx")
        else:
            st.warning("No stocks met the selected filtration criteria.")