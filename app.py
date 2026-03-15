import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from datetime import timedelta

# 設定網頁標題與寬度
st.set_page_config(page_title="BTC SMC 交易策略分析", layout="wide")

st.title("📈 Bitcoin SMC 交易策略分析儀表板")

# --- 畫面上顯示全部交易邏輯 ---
st.markdown("""
### 🧠 影片中的交易邏輯 (SMC 策略)
本圖表根據影片中 TJR 講解的 6 步驟策略進行了**程式化的萃取與實作**。由於完整的 SMC 包含許多主觀的流動性判斷，本程式使用局部高低點與 FVG (合理價值缺口) 作為觸發條件。

**影片原始策略核心步驟：**
1. **流動性清掃 (Liquidity Sweep)**：價格突破 1 小時、4 小時或特定時段的高低點後迅速反轉。
2. **5 分鐘確認訊號 (Confirmation Confluence)**：尋找結構突破 (BOS)、反向合理價值缺口 (IFVG) 或 SMT 背離。
    * *(2b) 如果清掃發生在盤前，需等待 5 分鐘的額外操縱行為。*
3. **5 分鐘延續訊號 (Continuation Confluence)**：價格回踩至平衡點 (EQ) 或合理價值缺口 (FVG)。
4. **進場 (Enter)**：確認上述條件後進場。
5. **目標 (Target)**：止盈設置於順勢方向的前期流動性（前高/前低）。

---
**💻 本程式圖表觸發邏輯 (簡化實作)：**
- **做多訊號 (Buy)**：找出近 10 根 K 線的最低點(視為向下清掃流動性)。當隨後出現 **看漲 FVG (Fair Value Gap)** 時，視為結構轉變並產生做多提示。
- **做空訊號 (Sell)**：找出近 10 根 K 線的最高點(視為向上清掃流動性)。當隨後出現 **看跌 FVG** 時，產生做空提示。
- **止損 (SL)**：做多設於近期最低點稍微下方；做空設於近期最高點稍微上方。
- **止盈 (TP)**：以 1:2 的風險報酬比 (Risk:Reward) 自動計算出止盈價位。
""")

st.divider()

# --- 獲取數據函數 ---
@st.cache_data(ttl=300) # 每5分鐘重新抓取一次資料
def load_data():
    # 抓取 BTC 過去 5 天的 5 分鐘 K 線數據
    df = yf.download("BTC-USD", period="5d", interval="5m")
    # 處理 yfinance 新版欄位格式
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df = df.dropna()
    return df

# --- 計算訊號函數 ---
def generate_signals(df):
    signals =[]
    
    # 計算 FVG (合理價值缺口)
    # 看漲 FVG: 當前 K 線的最低價 > 往前數第 2 根 K 線的最高價 (並且前一根為陽線)
    bullish_fvg = (df['Low'] > df['High'].shift(2)) & (df['Close'].shift(1) > df['Open'].shift(1))
    # 看跌 FVG: 當前 K 線的最高價 < 往前數第 2 根 K 線的最低價 (並且前一根為陰線)
    bearish_fvg = (df['High'] < df['Low'].shift(2)) & (df['Close'].shift(1) < df['Open'].shift(1))
    
    last_signal_time = None
    
    for i in range(10, len(df)):
        current_time = df.index[i]
        
        # 避免短時間內重複產生太多訊號 (設定間隔至少 2 小時)
        if last_signal_time and (current_time - last_signal_time).total_seconds() < 7200:
            continue
            
        if bullish_fvg.iloc[i]:
            # 做多邏輯：找出近 10 根 K 線的最低點當作止損
            recent_low = float(df['Low'].iloc[i-10:i].min())
            entry_price = float(df['Close'].iloc[i])
            sl = recent_low * 0.9995 # 止損設在低點下方 0.05%
            risk = entry_price - sl
            tp = entry_price + (risk * 2) # 止盈設為 1:2 盈虧比
            
            if risk > 0:
                signals.append({'Date': current_time, 'Type': 'Buy', 'Entry': entry_price, 'SL': sl, 'TP': tp})
                last_signal_time = current_time
                
        elif bearish_fvg.iloc[i]:
            # 做空邏輯：找出近 10 根 K 線的最高點當作止損
            recent_high = float(df['High'].iloc[i-10:i].max())
            entry_price = float(df['Close'].iloc[i])
            sl = recent_high * 1.0005 # 止損設在高點上方 0.05%
            risk = sl - entry_price
            tp = entry_price - (risk * 2) # 止盈設為 1:2 盈虧比
            
            if risk > 0:
                signals.append({'Date': current_time, 'Type': 'Sell', 'Entry': entry_price, 'SL': sl, 'TP': tp})
                last_signal_time = current_time

    return pd.DataFrame(signals)

# --- 載入數據與產生訊號 ---
with st.spinner("正在抓取最新 BTC 數據..."):
    df = load_data()
    signals_df = generate_signals(df)

# --- 顯示近期交易訊號清單 ---
st.subheader("🚨 近期觸發交易訊號提示")
if not signals_df.empty:
    st.dataframe(
        signals_df.style.format({'Entry': '${:,.2f}', 'SL': '${:,.2f}', 'TP': '${:,.2f}'}),
        use_container_width=True
    )
else:
    st.info("目前盤面近期無符合 SMC 條件的交易訊號。")

# --- 繪製互動式圖表 ---
st.subheader("📊 BTC/USD 5分鐘 K線圖 (含止損止盈標示)")

fig = go.Figure(data=[go.Candlestick(
    x=df.index,
    open=df['Open'],
    high=df['High'],
    low=df['Low'],
    close=df['Close'],
    name='BTC/USD'
)])

if not signals_df.empty:
    buy_signals = signals_df[signals_df['Type'] == 'Buy']
    sell_signals = signals_df[signals_df['Type'] == 'Sell']
    
    # 標示做多進場點
    fig.add_trace(go.Scatter(
        x=buy_signals['Date'], y=buy_signals['Entry'],
        mode='markers', marker=dict(color='cyan', size=14, symbol='triangle-up', line=dict(width=2, color='black')),
        name='做多進場 (Buy)'
    ))
    
    # 標示做空進場點
    fig.add_trace(go.Scatter(
        x=sell_signals['Date'], y=sell_signals['Entry'],
        mode='markers', marker=dict(color='magenta', size=14, symbol='triangle-down', line=dict(width=2, color='black')),
        name='做空進場 (Sell)'
    ))
    
    # 在畫面上畫出每個訊號的 SL 和 TP 線
    for idx, row in signals_df.iterrows():
        start_date = row['Date']
        # 畫線向右延伸約 4 小時的長度以供辨識
        end_date = start_date + timedelta(hours=4) 
        
        # 止損線 (紅色)
        fig.add_shape(type="line",
            x0=start_date, y0=row['SL'], x1=end_date, y1=row['SL'],
            line=dict(color="red", width=2, dash="dot")
        )
        # 止盈線 (綠色)
        fig.add_shape(type="line",
            x0=start_date, y0=row['TP'], x1=end_date, y1=row['TP'],
            line=dict(color="green", width=2, dash="dot")
        )
        
        # 價格文字標籤
        fig.add_annotation(x=end_date, y=row['SL'], text=f"SL: {row['SL']:.1f}", showarrow=False, font=dict(color="red", size=12), xanchor="left")
        fig.add_annotation(x=end_date, y=row['TP'], text=f"TP: {row['TP']:.1f}", showarrow=False, font=dict(color="green", size=12), xanchor="left")

# 圖表樣式設定
fig.update_layout(
    height=700,
    template="plotly_dark", # 深色主題
    margin=dict(l=0, r=50, t=30, b=0),
    xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
)

# 在 Streamlit 上顯示圖表
st.plotly_chart(fig, use_container_width=True)

st.caption("免責聲明：此圖表為 SMC 理論之演算法簡化版，僅供學習程式語言與技術分析參考，不構成任何財務投資建議。")
