import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from google import genai
import time

# 1. 페이지 설정
st.set_page_config(page_title="AI 단타 분석기", page_icon="📈", layout="centered")

# 2. 전역 쿨타임 관리 (모든 사용자가 서버 자원을 공유)
@st.cache_resource
def get_global_tracker():
    return {"last_run_time": 0}

tracker = get_global_tracker()
COOLDOWN_LIMIT = 10 

# 3. 세션 스테이트 초기화
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "last_ticker" not in st.session_state:
    st.session_state.last_ticker = ""
if "is_running" not in st.session_state:
    st.session_state.is_running = False
if "error_message" not in st.session_state:
    st.session_state.error_message = None

# 4. API 키 및 클라이언트 설정
try:
    GEMINI_API_KEY = st.secrets["GEMINI_KEY"]
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception:
    st.error("API 키(Secrets) 설정이 누락되었습니다. Streamlit 설정에서 GEMINI_KEY를 확인해주세요.")
    st.stop()

# 5. 데이터 수집 및 보조지표 계산 함수
def get_stock_data(ticker, interval):
    period = "5d" if "m" in interval else "1mo"
    try:
        df = yf.download(ticker, period=period, interval=interval, prepost=True, progress=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df['SMA_5'] = ta.sma(df['Close'], length=5)
        df['SMA_20'] = ta.sma(df['Close'], length=20)
        bb = ta.bbands(df['Close'], length=20, std=2)
        if bb is not None:
            df['BB_Lower'] = bb.iloc[:, 0]
            df['BB_Upper'] = bb.iloc[:, 2]
        df['CCI'] = ta.cci(df['High'], df['Low'], df['Close'], length=14)
        stoch = ta.stoch(df['High'], df['Low'], df['Close'])
        if stoch is not None:
            df['Stoch_K'] = stoch.iloc[:, 0]
        return df.iloc[-1]
    except Exception:
        return None

# 6. 버튼 클릭 콜백
def start_analysis():
    st.session_state.is_running = True
    st.session_state.error_message = None

# 7. 웹 UI 구성
st.title("📈 AI 단타 분석기 (V3.4)")
st.write("실시간 지표와 거래량을 분석하여 정밀한 매매 전략을 도출합니다.")

ticker = st.text_input("분석할 미장 티커(Ticker)를 입력하세요", value="SOXL").upper()

# ★ 화면 영역을 미리 정의 (코드 실행 순서와 화면 표시 순서를 분리)
button_area = st.container()
result_area = st.container()

# --- 결과/에러를 먼저 렌더링 (sleep과 무관하게 즉시 화면에 표시됨) ---
with result_area:
    if st.session_state.error_message:
        st.error(st.session_state.error_message)

    if st.session_state.analysis_result:
        st.divider()
        st.success(f"[{st.session_state.last_ticker}] 분석 결과")
        st.markdown(st.session_state.analysis_result)

    st.caption("※ 이 분석은 투자 참고용이며, 모든 투자의 책임은 투자자 본인에게 있습니다.")

# --- 버튼 영역 (결과는 이미 위에서 렌더링 완료) ---
with button_area:
    current_time = time.time()
    elapsed = current_time - tracker["last_run_time"]
    remaining = int(COOLDOWN_LIMIT - elapsed)

    if st.session_state.is_running:
        # 분석 진행 중: 버튼 비활성화
        st.button("분석 엔진 가동 중...", disabled=True, key="running_btn")

        with st.spinner(f"[{ticker}] 상세 지표 및 거래량 분석 중..."):
            d1 = get_stock_data(ticker, "1m")

            if d1 is None:
                st.session_state.error_message = f"'{ticker}'의 데이터를 가져올 수 없습니다. 티커가 유효한지, 장 중인지 확인해주세요."
                st.session_state.is_running = False
                st.rerun()

            d5 = get_stock_data(ticker, "5m")
            d30 = get_stock_data(ticker, "30m")

            if d5 is None:
                st.session_state.error_message = f"'{ticker}'의 5분봉 데이터를 가져올 수 없습니다. 잠시 후 다시 시도해주세요."
                st.session_state.is_running = False
                st.rerun()

            # 30분봉 null 안전 처리
            if d30 is not None:
                line_30m = f"[30분봉] 가격: {d30['Close']:.2f}, 거래량: {d30['Volume']:,.0f}, 20이평: {d30['SMA_20']:.2f}"
            else:
                line_30m = "[30분봉] 데이터 없음 (장 시작 직후이거나 데이터 부족)"

            prompt = f"""
            너는 미국 주식 전문 트레이더야. [{ticker}]의 데이터를 보고 일 3% 수익 목표 단타 전략을 세워줘.

            [1분봉] 가격: {d1['Close']:.2f}, 거래량: {d1['Volume']:,.0f}, 5이평: {d1['SMA_5']:.2f}, 20이평: {d1['SMA_20']:.2f}, 스토캐스틱K: {d1['Stoch_K']:.2f}
            [5분봉] 가격: {d5['Close']:.2f}, 거래량: {d5['Volume']:,.0f}, CCI: {d5['CCI']:.2f}
            {line_30m}

            분석 요구사항:
            1. 거래량 추이: 현재 변동성이 유의미한 거래량을 동반한 진짜 움직임인지 분석해줘.
            2. 전략 제안: 구체적인 진입가, 목표가(3% 수익), 손절가를 제안해줘.
            """

            try:
                response = None
                for attempt in range(3):
                    try:
                        response = client.models.generate_content(model='gemini-2.5-flash-lite', contents=prompt)
                        break
                    except Exception as api_err:
                        err_str = str(api_err)
                        # 일일 한도 초과 → 재시도 의미 없음
                        if "PerDay" in err_str or "daily" in err_str.lower():
                            raise api_err
                        # 분당 제한(429) 또는 서버 과부하(503) → 15초 대기 후 재시도
                        if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                                or "503" in err_str or "UNAVAILABLE" in err_str):
                            if attempt < 2:
                                time.sleep(15)
                                continue
                        raise api_err

                if response:
                    st.session_state.analysis_result = response.text
                    st.session_state.last_ticker = ticker
                    tracker["last_run_time"] = time.time()
                else:
                    st.session_state.error_message = "⏳ API 요청이 반복 실패했습니다. 잠시 후 다시 시도해주세요."

            except Exception as e:
                err_str = str(e)
                if "PerDay" in err_str or "daily" in err_str.lower():
                    st.session_state.error_message = "🚫 금일 무료 토큰이 소진되었습니다. 내일 접속하세요."
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    st.session_state.error_message = "⏳ API 요청 한도에 도달했습니다. 1분 후 다시 시도해주세요. (429)"
                elif "503" in err_str or "UNAVAILABLE" in err_str:
                    st.session_state.error_message = "⏳ 잠시 사용자가 많아서 대기 중입니다. 잠시 후 다시 시도해주세요. (503)"
                else:
                    st.session_state.error_message = f"AI 분석 중 오류가 발생했습니다: {e}"

            st.session_state.is_running = False
            st.rerun()

    elif remaining > 0:
        # 쿨타임 중: 버튼 비활성화 + JS 카운트다운 + 끝나면 1회 rerun으로 버튼 활성화
        st.button("제미니 AI 분석 시작", disabled=True, key="wait_btn")
        import streamlit.components.v1 as components
        components.html(f"""
            <div id="cooldown" style="
                padding: 12px 16px;
                background-color: #e8f4f8;
                border-radius: 8px;
                font-family: -apple-system, sans-serif;
                font-size: 15px;
                color: #31708f;
            ">
                ⏳ 쿨타임 중입니다. <strong><span id="sec">{remaining}</span>초</strong> 후 분석 가능합니다.
            </div>
            <script>
                let sec = {remaining};
                const el = document.getElementById('sec');
                const cd = document.getElementById('cooldown');
                const timer = setInterval(() => {{
                    sec--;
                    if (sec <= 0) {{
                        clearInterval(timer);
                        cd.innerHTML = '✅ 분석 가능! 버튼을 활성화합니다...';
                        cd.style.backgroundColor = '#e8f5e9';
                        cd.style.color = '#2e7d32';
                    }} else {{
                        el.textContent = sec;
                    }}
                }}, 1000);
            </script>
        """, height=55)
        # ★ 결과는 이미 result_area에서 렌더링 완료 → sleep 중에도 화면에 보임
        time.sleep(remaining)
        st.rerun()  # 세션 유지 → 결과 보존, 버튼만 활성화

    else:
        # 대기 상태: 버튼 활성화 → 클릭 시 즉시 is_running=True
        st.button("제미니 AI 분석 시작", key="start_btn", on_click=start_analysis)