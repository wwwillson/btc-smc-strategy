import streamlit as st
import pandas as pd
import requests
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
1. **流動性清掃 (Liquidity Sweep)**：價格突破特定時段的高低點後迅速反轉。
2. **5 分鐘確認訊號 (Confirmation Confluence)**：尋找結構轉變與反向合理價值缺口 (IFVG)。
3. **5 分鐘延續訊號 (Continuation Confluence)**：價格回踩至平衡點 (EQ) 或合理價值缺口 (FVG)。
4. **進場與目標 (Enter & Target)**：進場後，止盈設置於順勢方向的前期流動性。

---
**💻 本程式圖表觸發邏輯 (簡化實作)：**
- **做多訊號 (Buy)**：向下清掃近期最低點流動性後，隨後出現 **看漲 FVG (Fair Value Gap)**，視為結構轉變並產生做多提示。
- **做空訊號 (Sell)**：向上清掃近期最高點流動性後，隨後出現 **看跌 FVG**，產生做空提示。
- **止損 (SL)**：做多設於近期最低點稍微下方；做空設於近期最高點稍微上方。
- **止盈 (TP)**：以 1:2 的風險報酬比 (Risk:Reward) 自動計算出止盈價位。
""")

st.divider()

# --- 獲取數據函數 (改用穩定且無限制的 Binance 幣安公開 API) ---
@st.cache_data(ttl=300) # 每5分鐘重新抓取一次資料
def load_data():
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": "BTCUSDT",
            "interval": "5m",
            "limit": 1000  # 抓取最近 1000 根 5 分鐘 K 線 (大約 3.4 天)
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        # 轉換為 Pandas DataFrame
        df = pd.DataFrame(data, columns=[
            'Open time', 'Open', 'High', 'Low', 'Close', 'Volume',
            'Close time', 'Quote asset volume', 'Number of trades',
            'Taker buy base asset volume', 'Taker buy quote asset volume', 'Ignore'
        ])
        # 將時間戳轉換為 datetime 並設定為索引 (加上 UTC+8 台灣時間調整)
        df['Date'] = pd.to_datetime(df['Open time'], unit='ms') + timedelta(hours=8)
        df.set_index('Date', inplace=True)
        
        # 轉換價格欄位為浮點數
        for col in['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = df[col].astype(float)
            
        return df
    except Exception as e:
        st.error(f"獲取數據失敗: {e}")
        return pd.DataFrame()

# --- 計算訊號函數 ---
def generate_signals(df):
    signals =[]
    if df.empty:
        return pd.DataFrame(signals)
        
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
with st.spinner("正在透過 Binance API 抓取最新 BTC 數據..."):
    df = load_data()
    signals_df = generate_signals(df)

# --- 顯示近期交易訊號清單 ---
st.subheader("🚨 近期觸發交易訊號提示")
if not signals_df.empty:
    st.dataframe(
        signals_df.style.format({'Entry': '${:,.2f}', 'SL': '${:,.2f}', 'TP': '${:,.2f}'}),
        width="stretch"  # 修正棄用語法
    )
else:
    st.info("目前盤面近期無符合 SMC 條件的交易訊號。")

# --- 繪製互動式圖表 ---
st.subheader("📊 BTC/USDT 5分鐘 K線圖 (含止損止盈標示)")

if not df.empty:
    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df['Open'],
        high=df['High'],
        low=df['Low'],
        close=df['Close'],
        name='BTC/USDT'
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

    # 在 Streamlit 上顯示圖表 (修正棄
