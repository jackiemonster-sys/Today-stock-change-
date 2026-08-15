import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import streamlit as st
import twstock

# 1. 頁面配置
st.set_page_config(
    page_title="台股 A/V 轉折與雙向 K 棒雷達",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


# 2. 核心轉折、均線與多空特定 K 棒計算函式
def analyze_turnaround(df, lookback_days=10):
    if len(df) < lookback_days + 1:
        return df

    # --- 均線計算 (MA) ---
    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA10"] = df["Close"].rolling(10).mean()
    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    # --- 成交量與爆量邏輯 ---
    df["MA5_Vol"] = df["Volume"].rolling(5).mean()
    df["Vol_Ratio_MA5"] = (df["Volume"] / df["MA5_Vol"]).round(2)
    df["Vol_Ratio_Prev"] = (df["Volume"] / df["Volume"].shift(1)).round(2)

    # 爆大量：大於昨日 2 倍 或 大於 5 日均量 2 倍
    df["Is_Volume_Spurt"] = (df["Volume"] >= df["Volume"].shift(1) * 2.0) | (
        df["Volume"] >= df["MA5_Vol"] * 2.0
    )

    # --- 特定 K 棒特徵計算 ---
    df["Body"] = (df["Close"] - df["Open"]).abs()  # 實體長度
    df["Lower_Shadow"] = (
        df[["Open", "Close"]].min(axis=1) - df["Low"]
    )  # 下影線
    df["Upper_Shadow"] = (
        df["High"] - df[["Open", "Close"]].max(axis=1)
    )  # 上影線
    df["Range"] = df["High"] - df["Low"]  # 高低差
    df["Pct_Change"] = (df["Close"] - df["Open"]) / df["Open"] * 100  # 漲跌幅%

    # --- 🟢 看多特定 K 棒 ---
    # 1. 爆量長紅防守價 (漲幅 >= 2.5% + 爆量 + 實體大於上下影線)
    df["Is_Big_Red"] = (
        (df["Close"] > df["Open"])
        & (df["Pct_Change"] >= 2.5)
        & df["Is_Volume_Spurt"]
        & (df["Body"] > df["Lower_Shadow"])
        & (df["Body"] > df["Upper_Shadow"])
    )
    # 記錄最新一次爆量長紅棒的低點作為「主力防守價」
    df["Defense_Price"] = df["Low"].where(df["Is_Big_Red"]).ffill()

    # 2. 槌子線：下影線長 (實體2倍以上)，上影線極短
    df["Is_Hammer"] = (
        (df["Lower_Shadow"] >= df["Body"] * 2.0)
        & (df["Upper_Shadow"] <= df["Body"] * 0.5)
        & (df["Range"] > 0)
    )

    # 3. 多頭吞噬：今日紅棒實體完全包覆昨日黑棒實體
    df["Is_Bullish_Engulfing"] = (
        (df["Close"] > df["Open"])
        & (df["Close"].shift(1) < df["Open"].shift(1))
        & (df["Open"] <= df["Close"].shift(1))
        & (df["Close"] >= df["Open"].shift(1))
    )

    # --- 🔴 看空特定 K 棒 ---
    # 4. 射擊之星 (倒槌子)：上影線長 (實體2倍以上)，下影線極短
    df["Is_Shooting_Star"] = (
        (df["Upper_Shadow"] >= df["Body"] * 2.0)
        & (df["Lower_Shadow"] <= df["Body"] * 0.5)
        & (df["Range"] > 0)
    )

    # 5. 空頭吞噬：今日黑棒實體完全包覆昨日紅棒實體
    df["Is_Bearish_Engulfing"] = (
        (df["Close"] < df["Open"])
        & (df["Close"].shift(1) > df["Open"].shift(1))
        & (df["Open"] >= df["Close"].shift(1))
        & (df["Close"] <= df["Open"].shift(1))
    )

    # --- 🛑 中立/變盤 K 棒 ---
    # 6. 十字星：實體極小 (小於高低差 10%)
    df["Is_Doji"] = (df["Body"] <= df["Range"] * 0.10) & (df["Range"] > 0)

    # --- N 日高低點與轉折邏輯 ---
    df["N_High"] = df["High"].rolling(lookback_days).max().shift(1)
    df["N_Low"] = df["Low"].rolling(lookback_days).min().shift(1)

    df["V_Turn"] = (
        (df["Low"] <= df["N_Low"])
        & (df["Close"] > df["Open"])
        & (df["Close"] > df["High"].shift(1))
        & (df["Volume"] > df["MA5_Vol"] * 1.2)
    )

    df["A_Turn"] = (
        (df["High"] >= df["N_High"])
        & (df["Close"] < df["Open"])
        & (df["Close"] < df["Low"].shift(1))
        & (df["Volume"] > df["MA5_Vol"] * 1.2)
    )
    return df


# 3. 官方 TWSE / TPEx API 資料抓取
@st.cache_data(ttl=600)
def fetch_stock_from_official_api(stock_id):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    today = datetime.datetime.now()
    all_data = []

    for i in range(6):
        date_dt = today - datetime.timedelta(days=i * 28)
        date_str = date_dt.strftime("%Y%m01")

        url_twse = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={stock_id}"
        try:
            res = requests.get(url_twse, headers=headers, timeout=5)
            data = res.json()
            if data.get("stat") == "OK" and "data" in data:
                all_data.extend(data["data"])
                continue
        except Exception:
            pass

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

    parsed_rows = []
    for row in all_data:
        try:
            date_parts = row[0].strip().split("/")
            if len(date_parts) != 3:
                continue
            year = int(date_parts[0]) + 1911
            month = int(date_parts[1])
            day = int(date_parts[2])
            dt = pd.Timestamp(year, month, day)

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
                    "Volume": vol,
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


# 動態取得熱門股
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


def check_stock_signal(ticker, name, lookback):
    try:
        df = fetch_stock_from_official_api(ticker)
        if df.empty or len(df) < lookback + 1:
            return None

        df = analyze_turnaround(df, lookback)
        latest = df.iloc[-1]

        if latest["Volume"] < 500000:
            return None

        k_pattern = []
        if latest["Is_Hammer"]:
            k_pattern.append("🔨 槌子線")
        if latest["Is_Bullish_Engulfing"]:
            k_pattern.append("🟢 多頭吞噬")
        if latest["Is_Shooting_Star"]:
            k_pattern.append("☄️ 射擊之星")
        if latest["Is_Bearish_Engulfing"]:
            k_pattern.append("🔴 空頭吞噬")
        if latest["Is_Doji"]:
            k_pattern.append("🛑 十字星")
        pattern_str = " / ".join(k_pattern) if k_pattern else "無特殊型態"

        if latest["V_Turn"]:
            return {
                "代號": ticker,
                "名稱": name,
                "類型": "🔥 V型低檔反轉",
                "收盤價": round(float(latest["Close"]), 2),
                "成交量(張)": int(latest["Volume"] / 1000),
                "較昨日": f"{latest['Vol_Ratio_Prev']}x",
                "K棒型態": pattern_str,
            }
        elif latest["A_Turn"]:
            return {
                "代號": ticker,
                "名稱": name,
                "類型": "🚨 A型高檔反轉",
                "收盤價": round(float(latest["Close"]), 2),
                "成交量(張)": int(latest["Volume"] / 1000),
                "較昨日": f"{latest['Vol_Ratio_Prev']}x",
                "K棒型態": pattern_str,
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
    st.title("🎯 今日熱門台股 A/V 轉折與雙向 K 棒選股")
    st.caption(
        "自動掃描市場熱門個股，找出今日爆量觸發 A 轉 / V 轉訊號及多空 K 棒型態標的。"
    )

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
                (code, codes[code].name)
                for code in target_codes
                if code in codes
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
                                [
                                    "代號",
                                    "名稱",
                                    "收盤價",
                                    "成交量(張)",
                                    "較昨日",
                                    "K棒型態",
                                ]
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
                                [
                                    "代號",
                                    "名稱",
                                    "收盤價",
                                    "成交量(張)",
                                    "較昨日",
                                    "K棒型態",
                                ]
                            ],
                            use_container_width=True,
                        )
                    else:
                        st.write("今日無符合標的")
            else:
                st.warning("今日掃描範圍內未發現顯著的 A/V 轉折標的。")

# ================= 模式二：個股轉折 K 線與雙向 K 棒防守監控 =================
else:
    st.title("📊 個股 A/V 轉折與多空 K 棒監控")
    stock_id = st.text_input(
        "輸入台股代號 (例如: 2330, 2603, 8069)", "2330"
    ).strip()

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

            close_price = float(latest["Close"])

            # 1. 今日特定 K 棒型態檢查
            k_patterns = []
            # 看多型態
            if latest["Is_Hammer"]:
                k_patterns.append("🔨 槌子線(止跌訊號)")
            if latest["Is_Bullish_Engulfing"]:
                k_patterns.append("🟢 多頭吞噬(強勢看多)")
            if latest["Is_Big_Red"]:
                k_patterns.append("🚩 爆量長紅棒")

            # 看空型態
            if latest["Is_Shooting_Star"]:
                k_patterns.append("☄️ 射擊之星(高檔賣壓)")
            if latest["Is_Bearish_Engulfing"]:
                k_patterns.append("🔴 空頭吞噬(見頂反轉)")

            # 中立
            if latest["Is_Doji"]:
                k_patterns.append("🛑 十字星(變盤訊號)")

            # 2. 頁面數據指標顯示
            col1, col2, col3 = st.columns(3)
            with col1:
                vol_sheets = int(latest["Volume"] / 1000)
                st.metric("收盤價", f"{close_price:.2f}")
                st.metric(
                    "成交量 (張)",
                    f"{vol_sheets:,}",
                    f"較昨日 {latest['Vol_Ratio_Prev']}x / 較均量 {latest['Vol_Ratio_MA5']}x",
                )

            with col2:
                if latest["V_Turn"]:
                    st.success("🔥 今日觸發：V 型低檔轉折！")
                elif latest["A_Turn"]:
                    st.error("🚨 今日觸發：A 型高檔轉折！")
                else:
                    st.info("今日無特別 A/V 轉折訊號")

                # K棒型態提示
                if k_patterns:
                    st.warning("💡 今日K棒特徵：" + "、".join(k_patterns))

            with col3:
                st.subheader("🛡️ 主力與均線防守價")
                # 顯示爆量長紅防守點
                if pd.notna(latest["Defense_Price"]):
                    def_p = latest["Defense_Price"]
                    status = (
                        "🟢 守住防守價"
                        if close_price >= def_p
                        else "🔴 跌破防守價 (停損警示)"
                    )
                    st.markdown(
                        f"**爆量長紅低點**: `{def_p:.2f}` ({status})"
                    )

                # 顯示月線防守點
                if pd.notna(latest["MA20"]):
                    ma20_p = latest["MA20"]
                    status_ma = (
                        "🟢 站上" if close_price >= ma20_p else "🔴 跌破"
                    )
                    st.markdown(f"**20MA月線**: `{ma20_p:.2f}` ({status_ma})")

            # 3. 繪製圖表：主圖(K線+均線+防守虛線+雙向K棒) + 副圖(成交量)
            fig = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.03,
                subplot_titles=(
                    f"{stock_id} K線與雙向K棒訊號",
                    "成交量 (張)與爆量訊號",
                ),
                row_heights=[0.7, 0.3],
            )

            # --- 主圖：K 線 ---
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
                ),
                row=1,
                col=1,
            )

            # 均線
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["MA5"],
                    mode="lines",
                    name="5MA",
                    line=dict(color="orange", width=1.2),
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["MA20"],
                    mode="lines",
                    name="20MA月線",
                    line=dict(color="blue", width=1.5),
                ),
                row=1,
                col=1,
            )

            # 爆量長紅防守階梯線 (紅色虛線)
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df["Defense_Price"],
                    mode="lines",
                    name="爆量長紅防守價",
                    line=dict(color="red", width=2, dash="dash"),
                ),
                row=1,
                col=1,
            )

            # --- 標記特定 K 棒型態 ---
            # 看多型態標記（低點下方）
            hammers = df[df["Is_Hammer"]]
            bull_engulfings = df[df["Is_Bullish_Engulfing"]]
            fig.add_trace(
                go.Scatter(
                    x=hammers.index,
                    y=hammers["Low"] * 0.99,
                    mode="markers",
                    name="🔨 槌子線",
                    marker=dict(symbol="circle", size=8, color="dodgerblue"),
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=bull_engulfings.index,
                    y=bull_engulfings["Low"] * 0.985,
                    mode="markers",
                    name="🟢 多頭吞噬",
                    marker=dict(symbol="diamond", size=9, color="darkgreen"),
                ),
                row=1,
                col=1,
            )

            # 看空型態標記（高點上方）
            shooting_stars = df[df["Is_Shooting_Star"]]
            bear_engulfings = df[df["Is_Bearish_Engulfing"]]
            fig.add_trace(
                go.Scatter(
                    x=shooting_stars.index,
                    y=shooting_stars["High"] * 1.01,
                    mode="markers",
                    name="☄️ 射擊之星",
                    marker=dict(symbol="circle", size=8, color="purple"),
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=bear_engulfings.index,
                    y=bear_engulfings["High"] * 1.015,
                    mode="markers",
                    name="🔴 空頭吞噬",
                    marker=dict(symbol="diamond", size=9, color="black"),
                ),
                row=1,
                col=1,
            )

            # A/V 轉折標記
            v_turns = df[df["V_Turn"]]
            a_turns = df[df["A_Turn"]]
            fig.add_trace(
                go.Scatter(
                    x=v_turns.index,
                    y=v_turns["Low"] * 0.975,
                    mode="markers",
                    name="🔥 V轉買點",
                    marker=dict(symbol="triangle-up", size=12, color="red"),
                ),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=a_turns.index,
                    y=a_turns["High"] * 1.025,
                    mode="markers",
                    name="🚨 A轉賣點",
                    marker=dict(
                        symbol="triangle-down", size=12, color="green"
                    ),
                ),
                row=1,
                col=1,
            )

            # --- 副圖：成交量柱狀圖 ---
            vol_colors = []
            for _, row in df.iterrows():
                if row["Is_Volume_Spurt"]:
                    vol_colors.append("#FFD700")  # 爆量：亮金黃
                elif row["Close"] >= row["Open"]:
                    vol_colors.append("rgba(255, 0, 0, 0.5)")  # 漲：淡紅
                else:
                    vol_colors.append("rgba(0, 128, 0, 0.5)")  # 跌：淡綠

            fig.add_trace(
                go.Bar(
                    x=df.index,
                    y=df["Volume"] / 1000,
                    name="成交量(張)",
                    marker_color=vol_colors,
                ),
                row=2,
                col=1,
            )

            fig.update_layout(
                height=650,
                xaxis_rangeslider_visible=False,
                margin=dict(l=10, r=10, t=35, b=10),
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
                ),
            )
            st.plotly_chart(fig, use_container_width=True)
