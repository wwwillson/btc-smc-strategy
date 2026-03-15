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

# ==========================================
# 側邊欄設定參數與重整按鈕
# ==========================================
st.sidebar.header("⚙️ 控制台")

# 新增：重新整理按鈕
if st.sidebar.button("🔄 重新整理最新資料", use_container_width=True):
    st.cache_data.clear()  # 清除 API 快取
    st.rerun()             # 強制重新整理畫面

st.sidebar.markdown("---")

# 讓使用者可以選擇資料來源
data_source = st.sidebar.radio("數據來源 (Data Source)",["Binance.US API (完美支援雲端部署)", "Yahoo Finance"])

if "Binance" in data_source:
    ticker = st.sidebar.text_input("交易對 (Ticker)", "BTCUSDT")
    timeframe = st.sidebar.selectbox("時區 (Timeframe)",["15m", "1h", "4h", "1d"], index=1)
else:
    ticker = st.sidebar.text_input("交易對 (Ticker)", "BTC-USD")
    timeframe = st.sidebar.selectbox("時區 (Timeframe)",["15m", "1h", "4h", "1d"], index=1)

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
# 資料獲取函數 (使用 Binance.US 避開雲端 IP 封鎖)
# ==========================================
@st.cache_data(ttl=300)
def load_binance_us_data(symbol, interval, limit=1000):
    url = "https://api.binance.us/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, params=params, headers=headers, timeout=10)
        res.raise_for_status()
        df = pd.DataFrame(res.json(), columns=[
            'Open time', 'Open', 'High', 'Low', 'Close', 'Volume', 
            'Close time', 'Quote asset vol', 'Trades', 
            'Taker buy base', 'Taker buy quote', 'Ignore'
        ])
        
        # 轉換為台灣時間 (UTC+8)
        df['date'] = pd.to_datetime(df['Open time'], unit='ms') + pd.Timedelta(hours=8)
        df.set_index('date', inplace=True)
        
        for col in['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = df[col].astype(float)
            
        return df
    except Exception as e:
        st.error(f"Binance.US API 連線失敗: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=600)
def load_yf_data(ticker, timeframe):
    period_map = {"15m": "60d", "1h": "730d", "4h": "730d", "1d": "max"}
    period = period_map.get(timeframe, "60d")
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    
    for _ in range(3):
        try:
            df = yf.download(ticker, period=period, interval=timeframe, session=session, progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                df.dropna(inplace=True)
                if df.index.tz is not None:
                    df.index = df.index.tz_convert('Asia/Taipei').tz_localize(None)
                return df
        except Exception:
            time.sleep(2)
    return pd.DataFrame()

# 載入資料
with st.spinner(f"正在從 {data_source.split(' ')[0]} 載入 {ticker} 最新數據..."):
    if "Binance" in data_source:
        df = load_binance_us_data(ticker, timeframe)
    else:
        df = load_yf_data(ticker, timeframe)

if df.empty:
    st.error("無法獲取資料，請檢查 Ticker 或稍後再試。")
    st.stop()

# ==========================================
# 核心演算法：尋找流動性、FVG、與 IFVG 訊號
# ==========================================
signals =[]
last_pivot_low = None
last_pivot_high = None
bearish_fvgs = []
bullish_fvgs =[]

for i in range(2, len(df)):
    # 1. 計算無未來函數的波段高低點
    if i >= 2 * swing_length:
        window_lows = df['Low'].iloc[i - 2 * swing_length : i + 1]
        window_highs = df['High'].iloc[i - 2 * swing_length : i + 1]
        
        if df['Low'].iloc[i - swing_length] == window_lows.min():
            last_pivot_low = df['Low'].iloc[i - swing_length]
        if df['High'].iloc[i - swing_length] == window_highs.max():
            last_pivot_high = df['High'].iloc[i - swing_length]
            
    # 2. 尋找 FVG
    if df['High'].iloc[i] < df['Low'].iloc[i-2]:
        bearish_fvgs.append({
            'index': i, 'date': df.index[i],
            'top': df['Low'].iloc[i-2], 'bot': df['High'].iloc[i], 'active': True
        })
        
    if df['Low'].iloc[i] > df['High'].iloc[i-2]:
        bullish_fvgs.append({
            'index': i, 'date': df.index[i],
            'top': df['Low'].iloc[i], 'bot': df['High'].iloc[i-2], 'active': True
        })
        
    # 3. 尋找 IFVG 突破與流動性掃平確認
    for fvg in bearish_fvgs:
        if fvg['active'] and df['Close'].iloc[i] > fvg['top']:
            fvg['active'] = False
            if last_pivot_low is not None:
                recent_low = df['Low'].iloc[max(0, fvg['index'] - swing_length) : i + 1].min()
                if recent_low < last_pivot_low:
                    entry = df['Close'].iloc[i]
                    sl = fvg['bot']
                    if entry > sl: 
                        tp = entry + risk_reward_ratio * (entry - sl)
                        signals.append({'type': 'BUY', 'date': df.index[i], 'entry': entry, 'sl': sl, 'tp': tp})

    for fvg in bullish_fvgs:
        if fvg['active'] and df['Close'].iloc[i] < fvg['bot']:
            fvg['active'] = False
            if last_pivot_high is not None:
                recent_high = df['High'].iloc[max(0, fvg['index'] - swing_length) : i + 1].max()
                if recent_high > last_pivot_high:
                    entry = df['Close'].iloc[i]
                    sl = fvg['top']
                    if entry < sl:
                        tp = entry - risk_reward_ratio * (sl - entry)
                        signals.append({'type': 'SELL', 'date': df.index[i], 'entry': entry, 'sl': sl, 'tp': tp})

    # 清理太舊的 FVG (保留近30根K線以內)
    bearish_fvgs =[f for f in bearish_fvgs if f['active'] and i - f['index'] < 30]
    bullish_fvgs =[f for f in bullish_fvgs if f['active'] and i - f['index'] < 30]

# ==========================================
# 圖表繪製與視覺化 (Plotly)
# ==========================================
st.subheader(f"📊 {ticker} 圖表與交易訊號")

# 切片顯示指定的 K 線數量
df_plot = df.iloc[-plot_rows:]
plot_signals = [s for s in signals if s['date'] >= df_plot.index[0]]

fig = go.Figure(data=[go.Candlestick(
    x=df_plot.index,
    open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'],
    name=ticker,
    increasing_line_color='#26a69a', decreasing_line_color='#ef5350'
)])

# 計算 K 線寬度
candle_width = df.index[1] - df.index[0] if len(df.index) > 1 else timedelta(hours=1)

for sig in plot_signals:
    x_start = sig['date']
    x_end = sig['date'] + (candle_width * 15)  # 視覺方塊向右延伸 15 根 K 線

    if sig['type'] == 'BUY':
        # 標示進場箭頭
        fig.add_annotation(x=x_start, y=sig['entry'], text="BUY", showarrow=True, arrowhead=1, 
                           arrowcolor="#00FF00", arrowsize=2, ax=0, ay=30, font=dict(color="#00FF00", size=12, weight="bold"))
        # 繪製 SL 區域 (紅色)
        fig.add_shape(type="rect", x0=x_start, y0=sig['sl'], x1=x_end, y1=sig['entry'], fillcolor="rgba(255, 0, 0, 0.2)", line_width=1, line_color="red")
        # 繪製 TP 區域 (綠色)
        fig.add_shape(type="rect", x0=x_start, y0=sig['entry'], x1=x_end, y1=sig['tp'], fillcolor="rgba(0, 255, 0, 0.2)", line_width=1, line_color="green")
        # 數值文字
        fig.add_annotation(x=x_end, y=sig['sl'], text=f"SL: {sig['sl']:.1f}", showarrow=False, font=dict(color="red"), xanchor="left")
        fig.add_annotation(x=x_end, y=sig['tp'], text=f"TP: {sig['tp']:.1f}", showarrow=False, font=dict(color="green"), xanchor="left")

    elif sig['type'] == 'SELL':
        # 標示進場箭頭
        fig.add_annotation(x=x_start, y=sig['entry'], text="SELL", showarrow=True, arrowhead=1, 
                           arrowcolor="#FF0000", arrowsize=2, ax=0, ay=-30, font=dict(color="#FF0000", size=12, weight="bold"))
        # 繪製 SL 區域 (紅色)
        fig.add_shape(type="rect", x0=x_start, y0=sig['entry'], x1=x_end, y1=sig['sl'], fillcolor="rgba(255, 0, 0, 0.2)", line_width=1, line_color="red")
        # 繪製 TP 區域 (綠色)
        fig.add_shape(type="rect", x0=x_start, y0=sig['tp'], x1=x_end, y1=sig['entry'], fillcolor="rgba(0, 255, 0, 0.2)", line_width=1, line_color="green")
        # 數值文字
        fig.add_annotation(x=x_end, y=sig['sl'], text=f"SL: {sig['sl']:.1f}", showarrow=False, font=dict(color="red"), xanchor="left")
        fig.add_annotation(x=x_end, y=sig['tp'], text=f"TP: {sig['tp']:.1f}", showarrow=False, font=dict(color="green"), xanchor="left")

fig.update_layout(
    template="plotly_dark",
    height=750,
    margin=dict(l=50, r=50, t=30, b=30),
    xaxis_rangeslider_visible=False,
    yaxis_title="Price (USD)",
    xaxis_title="Time (Taiwan GMT+8)"
)

st.plotly_chart(fig, use_container_width=True)

# 顯示最新的訊號表格
if plot_signals:
    st.subheader("📋 近期交易訊號列表")
    df_signals = pd.DataFrame(plot_signals)
    df_signals['date'] = df_signals['date'].dt.strftime('%Y-%m-%d %H:%M')
    df_signals = df_signals[['date', 'type', 'entry', 'sl', 'tp']]
    df_signals.columns =['台灣時間 (Time)', '方向 (Type)', '進場價 (Entry)', '止損 (SL)', '止盈 (TP)']
    st.dataframe(df_signals.iloc[::-1].reset_index(drop=True), use_container_width=True)
else:
    st.info("在目前顯示的K線範圍內，沒有觸發新的 IFVG 訊號。可以嘗試向左滑動圖表，或在左側邊欄增加『圖表顯示K線數量』。")

# 最底部提示最近更新時間
st.caption(f"🕒 最新資料更新時間: {pd.Timestamp.now('Asia/Taipei').strftime('%Y-%m-%d %H:%M:%S')}")
