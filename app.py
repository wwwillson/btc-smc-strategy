import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import time
import yfinance as yf
from datetime import timedelta

# 設定網頁版面
st.set_page_config(page_title="BTC IFVG 操縱策略", layout="wide")
st.title("📈 BTC 量化交易: IFVG 流動性操縱策略")

# 側邊欄設定參數
st.sidebar.header("⚙️ 參數設定")

# 讓使用者可以選擇資料來源 (改用不鎖雲端IP的 Bybit)
data_source = st.sidebar.radio("數據來源 (Data Source)", ["Bybit API (雲端部署推薦)", "Yahoo Finance"])

if "Bybit" in data_source:
    ticker = st.sidebar.text_input("交易對 (Ticker)", "BTCUSDT")
    timeframe = st.sidebar.selectbox("時區 (Timeframe)",["15m", "1h", "4h", "1d"], index=1)
else:
    ticker = st.sidebar.text_input("交易對 (Ticker)", "BTC-USD")
    timeframe = st.sidebar.selectbox("時區 (Timeframe)", ["15m", "1h", "4h", "1d"], index=1)

swing_length = st.sidebar.slider("流動性波段長度 (Swing Length)", 5, 50, 15, help="決定高低點的K線數量")
risk_reward_ratio = st.sidebar.slider("盈虧比 (R/R Ratio)", 1.0, 5.0, 2.0, 0.1, help="止盈距離為止損距離的幾倍")
plot_rows = st.sidebar.slider("圖表顯示K線數量", 100, 2000, 300)

st.header("📝 交易邏輯 (Strategy Logic)")
st.markdown("""
本策略基於機構訂單流的「流動性操縱與反向合理價值缺口 (IFVG)」：
1. **標記流動性 (Liquidity)**：尋找波段高低點，這些通常是散戶停損單聚集地。
2. **操縱 (Sweep)**：價格跌破支撐或突破壓力引發停損後，迅速反轉。
3. **尋找反向缺口 (IFVG)**：主力實體強勢收盤覆蓋了近期的合理價值缺口(FVG)。
4. **進場與風控**：突破缺口即進場，停損設於缺口另一緣，盈虧比 1:2。
""")

# ==========================================
# 資料獲取函數 (改用 Bybit 避開 Binance 451 封鎖)
# ==========================================
@st.cache_data(ttl=300)
def load_bybit_data(symbol, interval, limit=1000):
    # Bybit API 的時間刻度代號對應
    interval_map = {"15m": "15", "1h": "60", "4h": "240", "1d": "D"}
    bybit_interval = interval_map.get(interval, "60")
    
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",  # 使用永續合約數據，流動性最準確
        "symbol": symbol,
        "interval": bybit_interval,
        "limit": limit
    }
    
    try:
        res = requests
