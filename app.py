import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
import io
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Alignment, Font, Border, Side
import streamlit.components.v1 as components

# --- RS LOGIC ENGINE ---
class RSHeatmapScreener:
    def __init__(self, rs_threshold, lookback_months, output_history):
        self.rs_threshold = rs_threshold / 100.0
        self.lookback_months = lookback_months
        self.output_history = output_history
        self.index_ticker = "^NSEI"

    def fetch_data(self, tickers):
        all_tickers = tickers + [self.index_ticker]
        data = yf.download(all_tickers, period="2y", interval="1mo", auto_adjust=True)['Close']
        return data.dropna(how='all')

    def generate_matrix(self, price_df, sector_map):
        index_prices = price_df[self.index_ticker]
        stock_prices = price_df.drop(columns=[self.index_ticker])
        
        # 1. Identify and Sort Target Months Chronologically
        target_months = stock_prices.index[-self.output_history:]
        target_months = sorted(target_months) # Ensure chronological order
        month_headers = [d.strftime('%b-%y') for d in target_months]
        
        matrix_data = []
        for ticker in stock_prices.columns:
            clean_name = ticker.replace(".NS", "")
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
        
        if not matrix_data:
            return pd.DataFrame()

        # 2. Build DataFrame and Force Column Ordering
        df = pd.DataFrame(matrix_data)
        fixed_cols = ["Symbols", "Sector"]
        # Filter month_headers to only those that actually ended up in the DF
        existing_months = [m for m in month_headers if m in df.columns]
        df = df[fixed_cols + existing_months]
        
        return df.sort_values(by="Sector", ascending=True)

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
    st.header("⚙️ Settings")
    uploaded_files = st.file_uploader("Upload Sector CSVs", type="csv", accept_multiple_files=True)
    history_range = st.number_input("Display History (Months)", min_value=1, value=6)
    cutoff = st.number_input("RS Threshold %", min_value=50, max_value=100, value=90)
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
        with st.spinner('Analyzing market leadership...'):
            screener = RSHeatmapScreener(cutoff, 12, history_range)
            price_data = screener.fetch_data(unique_tickers)
            matrix_df = screener.generate_matrix(price_data, sector_map)

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

            # Excel Styling
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                matrix_df.to_excel(writer, index=False, sheet_name='Alpha_RS')
                ws = writer.sheets['Alpha_RS']
                blue_high, emerald_lead, zebra_grey, header_dark = PatternFill(start_color="0EA5E9", fill_type="solid"), PatternFill(start_color="10B981", fill_type="solid"), PatternFill(start_color="F1F5F9", fill_type="solid"), PatternFill(start_color="0F172A", fill_type="solid")
                
                for cell in ws[1]:
                    cell.fill, cell.font, cell.alignment = header_dark, Font(color="FFFFFF", bold=True), Alignment(horizontal='center')

                for r_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
                    is_even = r_idx % 2 == 0
                    for cell in row:
                        cell.alignment = Alignment(horizontal='center')
                        if is_even: cell.fill = zebra_grey
                        val = str(cell.value) if cell.value else ""
                        if "_" in val:
                            tag, num = val.split("_")
                            cell.value, cell.number_format = int(num), '0"%"'
                            cell.font = Font(color="FFFFFF", bold=(tag=="CYAN"))
                            cell.fill = blue_high if tag == "CYAN" else emerald_lead

                ws.column_dimensions['B'].width, ws.column_dimensions['C'].width = 22, 28
                for i in range(4, ws.max_column + 1): ws.column_dimensions[chr(64 + i)].width = 14

            st.download_button(label="📥 Download Styled Report", data=output.getvalue(), file_name="Alpha_RS_Report.xlsx")