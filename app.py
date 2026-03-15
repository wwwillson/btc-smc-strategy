import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import timedelta

# 設定網頁版面
st.set_page_config(page_title="BTC IFVG 操縱策略", layout="wide")

st.title("📈 BTC 量化交易: IFVG 流動性操縱策略")

# 側邊欄設定參數
st.sidebar.header("⚙️ 參數設定")
ticker = st.sidebar.text_input("交易對 (Ticker)", "BTC-USD")
timeframe = st.sidebar.selectbox("時區 (Timeframe)", ["15m", "1h", "4h", "1d"], index=1)
swing_length = st.sidebar.slider("流動性波段長度 (Swing Length)", 5, 50, 15, help="決定高低點的K線數量")
risk_reward_ratio = st.sidebar.slider("盈虧比 (R/R Ratio)", 1.0, 5.0, 2.0, 0.1, help="止盈距離為止損距離的幾倍")
plot_rows = st.sidebar.slider("圖表顯示K線數量", 100, 2000, 300)

# 時間轉換對應 yfinance 的 period
period_map = {"15m": "60d", "1h": "730d", "4h": "730d", "1d": "max"}
period = period_map[timeframe]

# 顯示交易邏輯說明
st.header("📝 交易邏輯 (Strategy Logic)")
st.markdown("""
本策略基於機構訂單流的「流動性操縱與反向合理價值缺口 (IFVG)」概念，執行步驟如下：
1. **標記流動性水位 (Liquidity Level)**：尋找明顯的波段高點/低點，這些位置通常聚集了散戶的停損單。
2. **等待操縱行為 (Manipulation / Sweep)**：當價格跌破支撐或突破壓力，引發散戶停損後迅速反轉，代表主力在利用流動性建倉。
3. **尋找反向缺口 (Inverse Fair Value Gap - IFVG)**：
   - **FVG (合理價值缺口)**：由三根K線組成，第一根高點與第三根低點無重疊。
   - **IFVG (反向缺口)**：當價格反轉並實體**強勢收盤覆蓋**該 FVG 的另一側時，確認主力方向。
4. **進場與風控 (Entry, SL, TP)**：
   - 🟢 **做多 (BUY)**：價格向上收盤突破「看跌FVG」的頂部時進場。停損 (SL) 設於缺口下緣，止盈 (TP) 設為 1:2 盈虧比。
   - 🔴 **做空 (SELL)**：價格向下收盤跌破「看漲FVG」的底部時進場。停損 (SL) 設於缺口上緣，止盈 (TP) 設為 1:2 盈虧比。
""")

@st.cache_data(ttl=900)
def load_data(ticker, period, interval):
    df = yf.download(ticker, period=period, interval=interval)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.dropna(inplace=True)
    return df

with st.spinner(f"正在載入 {ticker} 數據..."):
    df = load_data(ticker, period, timeframe)

if df.empty:
    st.error("無法獲取資料，請檢查 Ticker 或 Timeframe 設定。")
    st.stop()

# ==========================================
# 核心演算法：尋找流動性、FVG、與 IFVG 訊號
# ==========================================
signals =[]
last_pivot_low = None
last_pivot_high = None
bearish_fvgs =[]
bullish_fvgs =[]

for i in range(2, len(df)):
    # 1. 計算無未來函數的波段高低點 (Liquidity Levels)
    if i >= 2 * swing_length:
        window_lows = df['Low'].iloc[i - 2 * swing_length : i + 1]
        window_highs = df['High'].iloc[i - 2 * swing_length : i + 1]
        
        # 判斷中心點是否為最低/最高
        if df['Low'].iloc[i - swing_length] == window_lows.min():
            last_pivot_low = df['Low'].iloc[i - swing_length]
        if df['High'].iloc[i - swing_length] == window_highs.max():
            last_pivot_high = df['High'].iloc[i - swing_length]
            
    # 2. 尋找 FVG (合理價值缺口)
    # 看跌 FVG (Bearish FVG) -> 為了後續做多 IFVG 準備
    if df['High'].iloc[i] < df['Low'].iloc[i-2]:
        bearish_fvgs.append({
            'index': i, 'date': df.index[i],
            'top': df['Low'].iloc[i-2], 'bot': df['High'].iloc[i], 'active': True
        })
        
    # 看漲 FVG (Bullish FVG) -> 為了後續做空 IFVG 準備
    if df['Low'].iloc[i] > df['High'].iloc[i-2]:
        bullish_fvgs.append({
            'index': i, 'date': df.index[i],
            'top': df['Low'].iloc[i], 'bot': df['High'].iloc[i-2], 'active': True
        })
        
    # 3. 尋找 IFVG 突破與流動性操縱確認
    # 做多訊號 (BUY)
    for fvg in bearish_fvgs:
        if fvg['active'] and df['Close'].iloc[i] > fvg['top']:
            fvg['active'] = False
            if last_pivot_low is not None:
                # 檢查近期的低點是否有掃平流動性 (跌破上一個 Pivot Low)
                recent_low = df['Low'].iloc[max(0, fvg['index'] - swing_length) : i + 1].min()
                if recent_low < last_pivot_low:
                    entry = df['Close'].iloc[i]
                    sl = fvg['bot']  # 停損設在缺口下緣
                    if entry > sl: 
                        tp = entry + risk_reward_ratio * (entry - sl)
                        signals.append({'type': 'BUY', 'date': df.index[i], 'entry': entry, 'sl': sl, 'tp': tp, 'liq': last_pivot_low})

    # 做空訊號 (SELL)
    for fvg in bullish_fvgs:
        if fvg['active'] and df['Close'].iloc[i] < fvg['bot']:
            fvg['active'] = False
            if last_pivot_high is not None:
                # 檢查近期的高點是否有掃平流動性 (突破上一個 Pivot High)
                recent_high = df['High'].iloc[max(0, fvg['index'] - swing_length) : i + 1].max()
                if recent_high > last_pivot_high:
                    entry = df['Close'].iloc[i]
                    sl = fvg['top']  # 停損設在缺口上緣
                    if entry < sl:
                        tp = entry - risk_reward_ratio * (sl - entry)
                        signals.append({'type': 'SELL', 'date': df.index[i], 'entry': entry, 'sl': sl, 'tp': tp, 'liq': last_pivot_high})

    # 清理太舊的 FVG (保留近 30 根 K 線內的缺口，保持效能與邏輯準確)
    bearish_fvgs = [f for f in bearish_fvgs if f['active'] and i - f['index'] < 30]
    bullish_fvgs =[f for f in bullish_fvgs if f['active'] and i - f['index'] < 30]


# ==========================================
# 圖表繪製與視覺化 (Plotly)
# ==========================================
st.subheader(f"📊 {ticker} 圖表與交易訊號")

# 切片顯示指定的 K 線數量
df_plot = df.iloc[-plot_rows:]
plot_signals =[s for s in signals if s['date'] >= df_plot.index[0]]

fig = go.Figure(data=[go.Candlestick(
    x=df_plot.index,
    open=df_plot['Open'], high=df_plot['High'], low=df_plot['Low'], close=df_plot['Close'],
    name=ticker,
    increasing_line_color='#26a69a', decreasing_line_color='#ef5350'
)])

# 計算 K 線寬度，用於繪製矩形長度
candle_width = df.index[1] - df.index[0]

for sig in plot_signals:
    x_start = sig['date']
    # 畫出盈虧比方塊往右延伸 15 根 K 線的時間
    x_end = sig['date'] + (candle_width * 15) 

    if sig['type'] == 'BUY':
        # 進場點箭頭
        fig.add_annotation(x=x_start, y=sig['entry'], text="BUY", showarrow=True, arrowhead=1, 
                           arrowcolor="#00FF00", arrowsize=2, ax=0, ay=30, font=dict(color="#00FF00", size=12, weight="bold"))
        
        # 止損區塊 (紅)
        fig.add_shape(type="rect", x0=x_start, y0=sig['sl'], x1=x_end, y1=sig['entry'], fillcolor="rgba(255, 0, 0, 0.2)", line_width=1, line_color="red")
        # 止盈區塊 (綠)
        fig.add_shape(type="rect", x0=x_start, y0=sig['entry'], x1=x_end, y1=sig['tp'], fillcolor="rgba(0, 255, 0, 0.2)", line_width=1, line_color="green")
        
        # 標示價位
        fig.add_annotation(x=x_end, y=sig['sl'], text=f"SL: {sig['sl']:.1f}", showarrow=False, font=dict(color="red"), xanchor="left")
        fig.add_annotation(x=x_end, y=sig['tp'], text=f"TP: {sig['tp']:.1f}", showarrow=False, font=dict(color="green"), xanchor="left")

    elif sig['type'] == 'SELL':
        # 進場點箭頭
        fig.add_annotation(x=x_start, y=sig['entry'], text="SELL", showarrow=True, arrowhead=1, 
                           arrowcolor="#FF0000", arrowsize=2, ax=0, ay=-30, font=dict(color="#FF0000", size=12, weight="bold"))
        
        # 止損區塊 (紅)
        fig.add_shape(type="rect", x0=x_start, y0=sig['entry'], x1=x_end, y1=sig['sl'], fillcolor="rgba(255, 0, 0, 0.2)", line_width=1, line_color="red")
        # 止盈區塊 (綠)
        fig.add_shape(type="rect", x0=x_start, y0=sig['tp'], x1=x_end, y1=sig['entry'], fillcolor="rgba(0, 255, 0, 0.2)", line_width=1, line_color="green")
        
        # 標示價位
        fig.add_annotation(x=x_end, y=sig['sl'], text=f"SL: {sig['sl']:.1f}", showarrow=False, font=dict(color="red"), xanchor="left")
        fig.add_annotation(x=x_end, y=sig['tp'], text=f"TP: {sig['tp']:.1f}", showarrow=False, font=dict(color="green"), xanchor="left")

fig.update_layout(
    template="plotly_dark",
    height=750,
    margin=dict(l=50, r=50, t=30, b=30),
    xaxis_rangeslider_visible=False,
    yaxis_title="Price (USD)",
    xaxis_title="Time"
)

st.plotly_chart(fig, use_container_width=True)

# 顯示最新的訊號表格
if plot_signals:
    st.subheader("📋 近期交易訊號列表")
    df_signals = pd.DataFrame(plot_signals)
    df_signals['date'] = df_signals['date'].dt.strftime('%Y-%m-%d %H:%M')
    df_signals = df_signals[['date', 'type', 'entry', 'sl', 'tp']]
    df_signals.columns =['時間 (Time)', '方向 (Type)', '進場價 (Entry)', '止損 (SL)', '止盈 (TP)']
    st.dataframe(df_signals.iloc[::-1].reset_index(drop=True), use_container_width=True)
else:
    st.info("在目前顯示的K線範圍內，沒有觸發新的 IFVG 訊號。可以嘗試調整『參數』或『Timeframe』。")
