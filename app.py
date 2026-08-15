from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import twstock

# 1. 頁面配置
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


# 3. 官方 TWSE / TPEx API 資料抓取 (防 IP 封鎖核心機制)
@st.cache_data(ttl=600)
def fetch_stock_from_official_api(stock_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    today = datetime.datetime.now()
    all_data = []

    # 抓取最近 6 個月的資料（按月份遞減）
    for i in range(6):
        date_dt = today - datetime.timedelta(days=i * 28)
        date_str = date_dt.strftime("%Y%m01")

        # --- 嘗試 1: 證交所 (TWSE 上市) ---
        url_twse = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={stock_id}"
        try:
            res = requests.get(url_twse, headers=headers, timeout=5)
            data = res.json()
            if data.get("stat") == "OK" and "data" in data:
                all_data.extend(data["data"])
                continue
        except Exception:
            pass

        # --- 嘗試 2: 櫃買中心 (TPEx 上櫃) ---
        roc_year = date_dt.year - 1911
        roc_date_str = f"{roc_year}/{date_dt.strftime('%m')}"
        url_tpex = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={roc_date_str}&stkno={stock_id}"
        try:
            res = requests.get(url_tpex, headers=headers, timeout=5)
            data = res.json()
            if "aaData" in data and len(data["aaData"]) > 0:
                all_data.extend(data["aaData"])
        except Exception:
            pass

    if not all_data:
        return pd.DataFrame()

    # 轉為 DataFrame 並進行格式清洗
    parsed_rows = []
    for row in all_data:
        try:
            # 日期轉換 (如 113/08/15 -> 2024-08-15)
            date_parts = row[0].strip().split("/")
            if len(date_parts) != 3:
                continue
            year = int(date_parts[0]) + 1911
            month = int(date_parts[1])
            day = int(date_parts[2])
            dt = pd.Timestamp(year, month, day)

            # 清理千分位逗號
            vol = float(row[1].replace(",", ""))
            open_p = float(row[3].replace(",", ""))
            high_p = float(row[4].replace(",", ""))
            low_p = float(row[5].replace(",", ""))
            close_p = float(row[6].replace(",", ""))

            parsed_rows.append(
                {
                    "Date": dt,
                    "Open": open_p,
                    "High": high_p,
                    "Low": low_p,
                    "Close": close_p,
                    "Volume": vol,  # 成交股數
                }
            )
        except Exception:
            continue

    if not parsed_rows:
        return pd.DataFrame()

    df = pd.DataFrame(parsed_rows)
    df = df.drop_duplicates(subset=["Date"]).sort_values("Date")
    df.set_index("Date", inplace=True)
    return df


# 動態取得證交所熱門股
@st.cache_data(ttl=1800)
def get_top_200_stocks_by_volume():
    TOP_200_VOLUME_STOCKS = [
        ("2330", "台積電"),
        ("2317", "鴻海"),
        ("2454", "聯發科"),
        ("2382", "廣達"),
        ("3231", "緯創"),
        ("2356", "英業達"),
        ("2376", "技嘉"),
        ("2303", "聯電"),
        ("2308", "台達電"),
        ("2603", "長榮"),
        ("2609", "陽明"),
        ("2615", "萬海"),
        ("2618", "長榮航"),
        ("2881", "富邦金"),
        ("2882", "國泰金"),
        ("2891", "中信金"),
    ]

    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = requests.get(url, headers=headers, timeout=4)

        if res.status_code == 200:
            data = res.json()
            df = pd.DataFrame(data)
            df = df[df["Code"].str.len() == 4]
            df["TradeVolume"] = (
                pd.to_numeric(df["TradeVolume"], errors="coerce").fillna(0)
            )
            df_top = df.sort_values(by="TradeVolume", ascending=False).head(
                200
            )
            api_list = [
                (row["Code"], row["Name"]) for _, row in df_top.iterrows()
            ]

            if len(api_list) >= 50:
                return api_list
    except Exception:
        pass

    return TOP_200_VOLUME_STOCKS


# 掃描並檢查單一股票轉折訊號
def check_stock_signal(ticker, name, lookback):
    try:
        df = fetch_stock_from_official_api(ticker)
        if df.empty or len(df) < lookback + 1:
            return None

        df = analyze_turnaround(df, lookback)
        latest = df.iloc[-1]

        # 排除日成交量低於 500 張的股票
        if latest["Volume"] < 500000:
            return None

        if latest["V_Turn"]:
            return {
                "代號": ticker,
                "名稱": name,
                "類型": "🔥 V型低檔反轉",
                "收盤價": round(float(latest["Close"]), 2),
                "成交量(張)": int(latest["Volume"] / 1000),
            }
        elif latest["A_Turn"]:
            return {
                "代號": ticker,
                "名稱": name,
                "類型": "🚨 A型高檔反轉",
                "收盤價": round(float(latest["Close"]), 2),
                "成交量(張)": int(latest["Volume"] / 1000),
            }
    except Exception:
        return None
    return None


# 4. 側邊欄控制區
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
                (code, codes[code].name) for code in target_codes if code in codes
            ]
        else:
            stock_list = get_top_200_stocks_by_volume()

        if not stock_list:
            st.warning("無法順利取得股票清單，請稍後再試。")
        else:
            results = []
            progress_bar = st.progress(0)
            total = len(stock_list)

            with ThreadPoolExecutor(max_workers=5) as executor:
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
    stock_id = st.text_input("輸入台股代號 (例如: 2330, 2603, 8069)", "2330").strip()

    if stock_id:
        with st.spinner("從台灣證交所 / 櫃買中心抓取最新資料中..."):
            df = fetch_stock_from_official_api(stock_id)

        if df.empty or len(df) < lookback + 1:
            st.error("查無資料或 K 線歷史天數不足，請確認股票代號是否正確。")
        else:
            df = analyze_turnaround(df, lookback)
            latest = df.iloc[-1]
            prev_date = df.index[-1].strftime("%Y-%m-%d")

            st.write(f"### 最新交易日：{prev_date}")

            col1, col2 = st.columns(2)
            with col1:
                st.metric("收盤價", f"{float(latest['Close']):.2f}")
            with col2:
                if latest["V_Turn"]:
                    st.success("🔥 今日觸發：V 型低檔轉折！")
                elif latest["A_Turn"]:
                    st.error("🚨 今日觸發：A 型高檔轉折！")
                else:
                    st.info("今日無特別 A/V 轉折訊號")

            # 繪製 K 線圖
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
