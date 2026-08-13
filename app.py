from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import twstock
import yfinance as yf

# 1. 頁面配置（針對手機介面優化）
st.set_page_config(
    page_title="台股 A/V 轉折雷達",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# 2. 核心轉折邏輯計算函式
def analyze_turnaround(df, lookback_days=10):
    if len(df) < lookback_days + 1:
        return df

    # 計算 5 日成交均量與 N 日高低點
    df["MA5_Vol"] = df["Volume"].rolling(5).mean()
    df["N_High"] = df["High"].rolling(lookback_days).max().shift(1)
    df["N_Low"] = df["Low"].rolling(lookback_days).min().shift(1)

    # V 轉邏輯：創低 + 陽線 + 突破前高 + 爆量 (1.2倍5日均量)
    df["V_Turn"] = (
        (df["Low"] <= df["N_Low"])
        & (df["Close"] > df["Open"])
        & (df["Close"] > df["High"].shift(1))
        & (df["Volume"] > df["MA5_Vol"] * 1.2)
    )

    # A 轉邏輯：創高 + 陰線 + 跌破前低 + 爆量 (1.2倍5日均量)
    df["A_Turn"] = (
        (df["High"] >= df["N_High"])
        & (df["Close"] < df["Open"])
        & (df["Close"] < df["Low"].shift(1))
        & (df["Volume"] > df["MA5_Vol"] * 1.2)
    )
    return df


# 動態取得證交所當日成交量前 200 名熱門股票
@st.cache_data(ttl=3600)
def get_top_200_stocks_by_volume():
    try:
        url = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10)
        data = res.json()

        # 找到包含股票個股交易資訊的 key (通常為 data9)
        raw_data = None
        for key in data.keys():
            if key.startswith("data") and isinstance(data[key], list):
                if len(data[key]) > 0 and len(data[key][0]) >= 16:
                    raw_data = data[key]
                    break

        if not raw_data:
            return []

        # 整理為 DataFrame
        df_raw = pd.DataFrame(raw_data)
        # 欄位索引 0: 證券代號, 1: 證券名稱, 2: 成交股數
        df_raw = df_raw[[0, 1, 2]].copy()
        df_raw.columns = ["code", "name", "volume"]

        # 過濾僅保留 4 碼普通股
        df_raw = df_raw[df_raw["code"].str.len() == 4]
        df_raw["volume"] = (
            df_raw["volume"].str.replace(",", "").astype(int)
        )

        # 按成交量降冪排序，取前 200 名
        df_top200 = df_raw.sort_values(by="volume", ascending=False).head(200)

        # 組成分構列表 [(代號.TW, 名稱)]
        return [
            (f"{row['code']}.TW", row["name"])
            for _, row in df_top200.iterrows()
        ]
    except Exception as e:
        st.error(f"無法取得證交所熱門股資訊: {e}")
        return []


# 單支股票資料下載 (快取 1 小時)
@st.cache_data(ttl=3600)
def fetch_single_stock(symbol):
    df = yf.download(symbol, period="6m", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# 掃描並檢查單一股票轉折訊號
def check_stock_signal(ticker, name, lookback):
    try:
        df = yf.download(ticker, period="1m", progress=False)
        if df.empty or len(df) < lookback + 1:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = analyze_turnaround(df, lookback)
        latest = df.iloc[-1]

        # 排除日成交量低於 500 張的低流動性股票
        if latest["Volume"] < 500000:
            return None

        code_clean = ticker.split(".")[0]
        if latest["V_Turn"]:
            return {
                "代號": code_clean,
                "名稱": name,
                "類型": "🔥 V型低檔反轉",
                "收盤價": round(latest["Close"], 2),
                "成交量(張)": int(latest["Volume"] / 1000),
            }
        elif latest["A_Turn"]:
            return {
                "代號": code_clean,
                "名稱": name,
                "類型": "🚨 A型高檔反轉",
                "收盤價": round(latest["Close"], 2),
                "成交量(張)": int(latest["Volume"] / 1000),
            }
    except Exception:
        return None
    return None


# 3. 側邊欄控制區
st.sidebar.title("🔍 轉折雷達設定")
mode = st.sidebar.radio(
    "選擇模式", ["全市場當日轉折選股", "個股轉折 K 線圖查詢"]
)
lookback = st.sidebar.slider(
    "轉折參考天數 (N日高低點)", min_value=5, max_value=30, value=10
)

# ================= 模式一：全市場當日轉折選股 =================
if mode == "全市場當日轉折選股":
    st.title("🎯 今日熱門台股 A/V 轉折選股")
    st.caption("自動掃描市場熱門個股，找出今日爆量觸發 A 轉 / V 轉訊號的標的。")

    market_type = st.selectbox(
        "選擇掃描範圍",
        ["台灣50指數成分股 (快速)", "當日成交量前 200 大熱門股 (證交所)"],
    )

    if st.button("🚀 開始掃描", use_container_width=True):
        st.info("掃描中，請稍候...")

        stock_list = []
        if market_type == "台灣50指數成分股 (快速)":
            codes = twstock.codes
            target_codes = [
                "2330",
                "2317",
                "2454",
                "2308",
                "2382",
                "2303",
                "2881",
                "2882",
                "2891",
                "3711",
                "1216",
                "2886",
                "2002",
                "2884",
                "2885",
                "5880",
                "2892",
                "3008",
                "2357",
                "2324",
            ]
            stock_list = [
                (f"{code}.TW", codes[code].name)
                for code in target_codes
                if code in codes
            ]
        else:
            stock_list = get_top_200_stocks_by_volume()

        if not stock_list:
            st.warning("無法順利取得股票清單，請稍後再試。")
        else:
            # 多線程加速掃描
            results = []
            progress_bar = st.progress(0)
            total = len(stock_list)

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(
                        check_stock_signal, ticker, name, lookback
                    )
                    for ticker, name in stock_list
                ]
                for idx, future in enumerate(as_completed(futures)):
                    res = future.result()
                    if res:
                        results.append(res)
                    progress_bar.progress((idx + 1) / total)

            progress_bar.empty()

            # 顯示結果
            if results:
                res_df = pd.DataFrame(results)
                st.success(
                    f"掃描完成！在 {total} 檔標的中發現 {len(res_df)} 檔出現轉折訊號。"
                )

                v_df = res_df[res_df["類型"].str.contains("V型")]
                a_df = res_df[res_df["類型"].str.contains("A型")]

                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("🔥 V 型反轉（看多）")
                    if not v_df.empty:
                        st.dataframe(
                            v_df[
                                ["代號", "名稱", "收盤價", "成交量(張)"]
                            ],
                            use_container_width=True,
                        )
                    else:
                        st.write("今日無符合標的")

                with col2:
                    st.subheader("🚨 A 型反轉（看空）")
                    if not a_df.empty:
                        st.dataframe(
                            a_df[
                                ["代號", "名稱", "收盤價", "成交量(張)"]
                            ],
                            use_container_width=True,
                        )
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

        # 若上市抓不到，嘗試上櫃 .TWO
        if df.empty:
            ticker = f"{stock_id}.TWO"
            df = fetch_single_stock(ticker)

        if df.empty:
            st.error("查無資料，請確認股票代號是否正確。")
        else:
            df = analyze_turnaround(df, lookback)
            latest = df.iloc[-1]
            prev_date = df.index[-1].strftime("%Y-%m-%d")

            st.write(f"### 最新交易日：{prev_date}")

            col1, col2 = st.columns(2)
            with col1:
                st.metric("收盤價", f"{latest['Close']:.2f}")
            with col2:
                if latest["V_Turn"]:
                    st.success("🔥 今日觸發：V 型低檔轉折！")
                elif latest["A_Turn"]:
                    st.error("🚨 今日觸發：A 型高檔轉折！")
                else:
                    st.info("今日無特別 A/V 轉折訊號")

            # 繪製 Plotly K 線圖
            fig = go.Figure()
            fig.add_trace(
                go.Candlestick(
                    x=df.index,
                    open=df["Open"],
                    high=df["High"],
                    low=df["Low"],
                    close=df["Close"],
                    name="K線",
                    increasing_line_color="red",
                    decreasing_line_color="green",
                )
            )

            # 標註歷史轉折訊號點
            v_turns = df[df["V_Turn"]]
            a_turns = df[df["A_Turn"]]

            fig.add_trace(
                go.Scatter(
                    x=v_turns.index,
                    y=v_turns["Low"] * 0.98,
                    mode="markers",
                    name="V轉買點",
                    marker=dict(symbol="triangle-up", size=12, color="red"),
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=a_turns.index,
                    y=a_turns["High"] * 1.02,
                    mode="markers",
                    name="A轉賣點",
                    marker=dict(
                        symbol="triangle-down", size=12, color="green"
                    ),
                )
            )

            fig.update_layout(
                title=f"{stock_id} 轉折訊號 K 線圖",
                xaxis_rangeslider_visible=False,
                height=450,
                margin=dict(l=10, r=10, t=35, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)
