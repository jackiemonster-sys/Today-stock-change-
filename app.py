import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import twstock
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 頁面配置（針對手機優化）
st.set_page_config(
    page_title="台股 A/V 轉折雷達",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. 核心轉折邏輯計算函式
def analyze_turnaround(df, lookback_days=10):
    if len(df) < lookback_days + 1:
        return df

    # 計算 5 日成交均量與 N 日高低點
    df['MA5_Vol'] = df['Volume'].rolling(5).mean()
    df['N_High'] = df['High'].rolling(lookback_days).max().shift(1)
    df['N_Low'] = df['Low'].rolling(lookback_days).min().shift(1)

    # V 轉邏輯：創低 + 陽線 + 突破前高 + 爆量 (1.2倍5日均量)
    df['V_Turn'] = (
        (df['Low'] <= df['N_Low']) & 
        (df['Close'] > df['Open']) & 
        (df['Close'] > df['High'].shift(1)) & 
        (df['Volume'] > df['MA5_Vol'] * 1.2)
    )

    # A 轉邏輯：創高 + 陰線 + 跌破前低 + 爆量 (1.2倍5日均量)
    df['A_Turn'] = (
        (df['High'] >= df['N_High']) & 
        (df['Close'] < df['Open']) & 
        (df['Close'] < df['Low'].shift(1)) & 
        (df['Volume'] > df['MA5_Vol'] * 1.2)
    )
    return df

# 單支股票資料下載
@st.cache_data(ttl=3600)
def fetch_single_stock(symbol):
    df = yf.download(symbol, period="6m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

# 全市場個股掃描輔助函式
def check_stock_signal(ticker, name, lookback):
    try:
        df = yf.download(ticker, period="1m", progress=False)
        if df.empty or len(df) < lookback + 1:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        df = analyze_turnaround(df, lookback)
        latest = df.iloc[-1]
        
        # 過濾成交量過小的流動性差股票 (例如今日成交量大於 500 張)
        if latest['Volume'] < 500000:
            return None

        if latest['V_Turn']:
            return {"代號": ticker.split('.')[0], "名稱": name, "類型": "🔥 V型低檔反轉", "收盤價": round(latest['Close'], 2), "成交量(張)": int(latest['Volume']/1000)}
        elif latest['A_Turn']:
            return {"代號": ticker.split('.')[0], "名稱": name, "類型": "🚨 A型高檔反轉", "收盤價": round(latest['Close'], 2), "成交量(張)": int(latest['Volume']/1000)}
    except Exception:
        return None
    return None

# 3. 側邊欄與選單
st.sidebar.title("🔍 選單與設定")
mode = st.sidebar.radio("選擇模式", ["全市場當日轉折選股", "個股轉折 K 線圖查詢"])
lookback = st.sidebar.slider("轉折參考天數 (N日高低點)", min_value=5, max_value=30, value=10)

# ================= 模式一：全市場當日轉折選股 =================
if mode == "全市場當日轉折選股":
    st.title("🎯 今日全台股 A/V 轉折選股")
    st.caption("自動掃描熱門上市股票，找出今日爆量觸發轉折訊號的標的。")

    market_type = st.selectbox("選擇掃描範圍", ["台灣50指數成分股 (快速)", "熱門上市股票 (前200檔)"])

    if st.button("🚀 開始掃描", use_container_width=True):
        st.info("掃描中，請稍候...")
        
        # 獲取股票清單
        stock_list = []
        codes = twstock.codes
        
        if market_type == "台灣50指數成分股 (快速)":
            # 簡化常用權值股清單
            target_codes = ["2330", "2317", "2454", "2308", "2382", "2303", "2881", "2882", "2891", "3711", "1216", "2886", "2002", "2884", "2885", "5880", "2892", "3008", "2357", "2324"]
            stock_list = [(f"{code}.TW", codes[code].name) for code in target_codes if code in codes]
        else:
            # 篩選上市普通股
            count = 0
            for code, info in codes.items():
                if info.type == '股票' and info.market == '上市' and len(code) == 4:
                    stock_list.append((f"{code}.TW", info.name))
                    count += 1
                    if count >= 200: # 限制前 200 檔確保體驗流暢
                        break

        # 多執行緒加速掃描
        results = []
        progress_bar = st.progress(0)
        total = len(stock_list)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_stock_signal, ticker, name, lookback) for ticker, name in stock_list]
            for idx, future in enumerate(as_completed(futures)):
                res = future.result()
                if res:
                    results.append(res)
                progress_bar.progress((idx + 1) / total)

        progress_bar.empty()

        # 展示結果
        if results:
            res_df = pd.DataFrame(results)
            st.success(f"掃描完成！共發現 {len(res_df)} 檔轉折個股。")
            
            # 分頁分開顯示 V 轉與 A 轉
            v_df = res_df[res_df['類型'].str.contains("V型")]
            a_df = res_df[res_df['類型'].str.contains("A型")]

            col1, col2 = st.columns(2)
            with col1:
                st.subheader("🔥 V 型反轉（看多）")
                if not v_df.empty:
                    st.dataframe(v_df[['代號', '名稱', '收盤價', '成交量(張)']], use_container_width=True)
                else:
                    st.write("今日無符合標的")

            with col2:
                st.subheader("🚨 A 型反轉（看空）")
                if not a_df.empty:
                    st.dataframe(a_df[['代號', '名稱', '收盤價', '成交量(張)']], use_container_width=True)
                else:
                    st.write("今日無符合標的")
        else:
            st.warning("今日掃描範圍內未發現顯著的 A/V 轉折標的。")

# ================= 模式二：個股轉折 K 線圖查詢 =================
else:
    st.title("📊 個股 A/V 轉折 K 線監控")
    stock_id = st.text_input("輸入台股代號 (例如: 2330, 2603)", "2330")
    
    if stock_id:
        ticker = f"{stock_id}.TW"
        df = fetch_single_stock(ticker)
        
        if df.empty:
            # 嘗試上櫃 .TWO
            ticker = f"{stock_id}.TWO"
            df = fetch_single_stock(ticker)

        if df.empty:
            st.error("查無資料，請確認股票代號是否正確。")
        else:
            df = analyze_turnaround(df, lookback)
            latest = df.iloc[-1]
            prev_date = df.index[-1].strftime('%Y-%m-%d')
            
            st.write(f"### 最新交易日：{prev_date}")
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("收盤價", f"{latest['Close']:.2f}")
            with col2:
                if latest['V_Turn']:
                    st.success("🔥 今日觸發：V 型低檔轉折！")
                elif latest['A_Turn']:
                    st.error("🚨 今日觸發：A 型高檔轉折！")
                else:
                    st.info("今日無特別 A/V 轉折訊號")

            # Plotly 手機友好的互動式 K 線
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
                name="K線", increasing_line_color='red', decreasing_line_color='green'
            ))

            # 標註歷史 V 轉與 A 轉
            v_turns = df[df['V_Turn']]
            a_turns = df[df['A_Turn']]
            
            fig.add_trace(go.Scatter(
                x=v_turns.index, y=v_turns['Low'] * 0.98,
                mode='markers', name='V轉點', marker=dict(symbol='triangle-up', size=12, color='red')
            ))
            fig.add_trace(go.Scatter(
                x=a_turns.index, y=a_turns['High'] * 1.02,
                mode='markers', name='A轉點', marker=dict(symbol='triangle-down', size=12, color='green')
            ))

            fig.update_layout(
                title=f"{stock_id} 轉折訊號 K 線圖",
                xaxis_rangeslider_visible=False,
                height=450,
                margin=dict(l=10, r=10, t=35, b=10)
            )
            st.plotly_chart(fig, use_container_width=True)
