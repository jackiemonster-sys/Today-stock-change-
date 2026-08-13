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


# 動態取得證交所當日成交量前 200 名熱門股票 (帶精選熱門股 Fallback)
@st.cache_data(ttl=3600)
def get_top_200_stocks_by_volume():
    # 200 檔台股真實熱門成交量與權值股備援清單 (確保被擋 IP 時絕不按代碼排序)
    TOP_200_VOLUME_STOCKS = [
        ("2330.TW", "台積電"),
        ("2317.TW", "鴻海"),
        ("2454.TW", "聯發科"),
        ("2382.TW", "廣達"),
        ("3231.TW", "緯創"),
        ("2356.TW", "英業達"),
        ("2376.TW", "技嘉"),
        ("6669.TW", "緯穎"),
        ("2303.TW", "聯電"),
        ("2308.TW", "台達電"),
        ("2301.TW", "光寶科"),
        ("3711.TW", "日月光投控"),
        ("2603.TW", "長榮"),
        ("2609.TW", "陽明"),
        ("2615.TW", "萬海"),
        ("2618.TW", "長榮航"),
        ("2610.TW", "華航"),
        ("2881.TW", "富邦金"),
        ("2882.TW", "國泰金"),
        ("2891.TW", "中信金"),
        ("2886.TW", "兆豐金"),
        ("2884.TW", "玉山金"),
        ("2885.TW", "元大金"),
        ("5880.TW", "合庫金"),
        ("2892.TW", "第一金"),
        ("2880.TW", "華南金"),
        ("2883.TW", "開發金"),
        ("2887.TW", "台新金"),
        ("2890.TW", "永豐金"),
        ("2801.TW", "彰銀"),
        ("2002.TW", "中鋼"),
        ("1301.TW", "台塑"),
        ("1303.TW", "南亞"),
        ("1326.TW", "台化"),
        ("6505.TW", "台塑化"),
        ("1101.TW", "台泥"),
        ("1216.TW", "統一"),
        ("3008.TW", "大立光"),
        ("2357.TW", "華碩"),
        ("2324.TW", "仁寶"),
        ("2353.TW", "宏碁"),
        ("3037.TW", "欣興"),
        ("3034.TW", "聯詠"),
        ("2408.TW", "南亞科"),
        ("2379.TW", "瑞昱"),
        ("3045.TW", "台灣大"),
        ("4904.TW", "遠傳"),
        ("2412.TW", "中華電"),
        ("2345.TW", "智邦"),
        ("2327.TW", "國巨"),
        ("2377.TW", "微星"),
        ("2302.TW", "麗正"),
        ("2363.TW", "矽統"),
        ("2388.TW", "威盛"),
        ("2498.TW", "宏達電"),
        ("3017.TW", "奇鋐"),
        ("3324.TW", "雙鴻"),
        ("6278.TW", "台表科"),
        ("6176.TW", "瑞儀"),
        ("2352.TW", "佳世達"),
        ("2409.TW", "友達"),
        ("3481.TW", "群創"),
        ("6116.TW", "彩晶"),
        ("2312.TW", "金寶"),
        ("2354.TW", "鴻準"),
        ("2355.TW", "敬鵬"),
        ("2368.TW", "金像電"),
        ("2383.TW", "台光電"),
        ("6213.TW", "聯茂"),
        ("3035.TW", "智原"),
        ("3443.TW", "創意"),
        ("3661.TW", "世芯-KY"),
        ("6415.TW", "矽力*-KY"),
        ("3529.TW", "力旺"),
        ("6531.TW", "愛普*"),
        ("2455.TW", "全新"),
        ("3105.TW", "穩懋"),
        ("2451.TW", "創見"),
        ("2404.TW", "漢唐"),
        ("6187.TW", "萬潤"),
        ("3583.TW", "辛耘"),
        ("3131.TW", "弘塑"),
        ("2467.TW", "志聖"),
        ("1503.TW", "士電"),
        ("1504.TW", "東元"),
        ("1513.TW", "中興電"),
        ("1514.TW", "亞力"),
        ("1519.TW", "華城"),
        ("1605.TW", "華新"),
        ("1609.TW", "大亞"),
        ("8996.TW", "高力"),
        ("1802.TW", "台玻"),
        ("2105.TW", "正新"),
        ("2201.TW", "裕隆"),
        ("2204.TW", "中華"),
        ("2206.TW", "三陽工業"),
        ("2617.TW", "台航"),
        ("2637.TW", "慧洋-KY"),
        ("2605.TW", "新興"),
        ("2606.TW", "裕民"),
        ("2634.TW", "漢翔"),
        ("2630.TW", "亞航"),
        ("2707.TW", "晶華"),
        ("2727.TW", "王品"),
        ("2912.TW", "統一超"),
        ("8454.TW", "富邦媒"),
        ("9904.TW", "寶成"),
        ("9910.TW", "豐泰"),
        ("9921.TW", "巨大"),
        ("9914.TW", "美利達"),
        ("9958.TW", "世紀鋼"),
        ("3708.TW", "上緯投控"),
        ("6443.TW", "元晶"),
        ("3576.TW", "聯合再生"),
        ("2406.TW", "國碩"),
        ("2481.TW", "強茂"),
        ("5425.TW", "台半"),
        ("3675.TW", "德微"),
        ("6269.TW", "台郡"),
        ("4958.TW", "臻鼎-KY"),
        ("3036.TW", "文曄"),
        ("3702.TW", "大聯大"),
        ("2489.TW", "瑞軒"),
        ("2385.TW", "群光"),
        ("2395.TW", "研華"),
        ("2393.TW", "億光"),
        ("3019.TW", "亞光"),
        ("3406.TW", "玉晶光"),
        ("3376.TW", "新日興"),
        ("3533.TW", "嘉澤"),
        ("6271.TW", "同欣電"),
        ("3706.TW", "神達"),
        ("2313.TW", "華通"),
        ("2367.TW", "燿華"),
        ("3044.TW", "健鼎"),
        ("3030.TW", "德律"),
        ("2392.TW", "正崴"),
        ("3013.TW", "嘉雲"),
        ("2371.TW", "大同"),
        ("2374.TW", "佳能"),
        ("2480.TW", "敦陽科"),
        ("2474.TW", "可成"),
        ("3005.TW", "神基"),
        ("3211.TW", "順達"),
        ("6121.TW", "新普"),
        ("2401.TW", "凌陽"),
        ("2362.TW", "藍天"),
        ("2347.TW", "聯強"),
        ("2348.TW", "海悅"),
        ("2520.TW", "冠德"),
        ("2542.TW", "興富發"),
        ("2511.TW", "太子"),
        ("2548.TW", "華固"),
        ("5534.TW", "長虹"),
        ("1402.TW", "遠東新"),
        ("1476.TW", "儒鴻"),
        ("1477.TW", "聚陽"),
        ("1702.TW", "南僑"),
        ("1717.TW", "長興"),
        ("1722.TW", "台肥"),
        ("1795.TW", "美時"),
        ("4164.TW", "承業醫"),
        ("6472.TW", "保瑞"),
        ("4743.TW", "合一"),
        ("4128.TW", "中天"),
        ("1314.TW", "中石化"),
        ("1304.TW", "台聚"),
        ("1308.TW", "亞聚"),
        ("1305.TW", "華夏"),
        ("1710.TW", "東聯"),
        ("1907.TW", "永豐餘"),
        ("1904.TW", "正隆"),
        ("2006.TW", "東和鋼鐵"),
        ("2014.TW", "中鴻"),
        ("2027.TW", "大成鋼"),
        ("8464.TW", "億豐"),
        ("2106.TW", "建大"),
        ("2207.TW", "和泰車"),
        ("8926.TW", "台汽電"),
        ("6806.TW", "森崴能源"),
        ("8938.TW", "明安"),
        ("8924.TW", "大田"),
        ("5388.TW", "中磊"),
        ("6285.TW", "啟碁"),
        ("3596.TW", "智易"),
        ("2314.TW", "揚智"),
        ("2458.TW", "義隆"),
        ("3032.TW", "偉詮電"),
        ("6411.TW", "晶焱"),
        ("8016.TW", "高華"),
        ("3260.TW", "威剛"),
        ("2402.TW", "毅嘉"),
        ("2360.TW", "致茂"),
        ("3023.TW", "信邦"),
        ("3234.TW", "光環"),
        ("3081.TW", "聯亞"),
        ("4977.TW", "眾達-KY"),
        ("2340.TW", "台亞"),
        ("6209.TW", "今國光"),
        ("2486.TW", "一詮"),
        ("3653.TW", "健策"),
        ("3338.TW", "泰碩"),
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
                (f"{row['Code']}.TW", row["Name"])
                for _, row in df_top.iterrows()
            ]

            if len(api_list) >= 50:
                return api_list
    except Exception:
        pass

    return TOP_200_VOLUME_STOCKS


# 單支股票資料下載
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
