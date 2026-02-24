import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from google import genai
from datetime import datetime, timezone, timedelta
import time

# 1. 페이지 설정
st.set_page_config(page_title="AI 단타 분석기", page_icon="📈", layout="centered")

# 2. 전역 쿨타임 관리 (모든 사용자가 서버 자원을 공유)
@st.cache_resource
def get_global_tracker():
    return {"last_run_time": 0, "pro_exhausted": False}

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
if "history" not in st.session_state:
    st.session_state.history = []
if "used_model" not in st.session_state:
    st.session_state.used_model = ""
if "volume_unavailable" not in st.session_state:
    st.session_state.volume_unavailable = False
if "market_closed" not in st.session_state:
    st.session_state.market_closed = False

# 장 상태 판단 함수 (EST 기준)
def is_market_open():
    est = timezone(timedelta(hours=-5))
    now = datetime.now(est)
    # 주말 체크 (토=5, 일=6)
    if now.weekday() >= 5:
        return False
    # 정규장: 09:30 ~ 16:00 EST
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now <= market_close

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
        # ★ 마지막 봉이 미완성(거래량0)이고 직전 봉에 거래량이 있으면 → 완성된 봉 사용
        if df['Volume'].iloc[-1] == 0 and len(df) >= 2 and df['Volume'].iloc[-2] > 0:
            return df.iloc[-2]
        return df.iloc[-1]
    except Exception:
        return None

# 6. 버튼 클릭 콜백
def start_analysis():
    st.session_state.is_running = True
    st.session_state.error_message = None

# 7. 웹 UI 구성
st.title("📈 AI 단타 분석기 (V1.1)")
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
        if st.session_state.market_closed:
            st.info("📢 현재 장이 마감된 상태입니다. 마감 전 마지막 데이터로 분석되었습니다.")
        if st.session_state.volume_unavailable:
            st.warning("📌 프리마켓/애프터마켓 시간대로 거래량 데이터가 제공되지 않습니다. 가격 기반 분석만 수행되었습니다.")
        st.success(f"[{st.session_state.last_ticker}] 분석 결과 — 엔진: {st.session_state.used_model}")
        st.markdown(st.session_state.analysis_result)

    # ★ 히스토리 목록 (최근 10개)
    if len(st.session_state.history) > 1:
        st.divider()
        with st.expander(f"📋 이전 분석 기록 ({len(st.session_state.history) - 1}건)", expanded=False):
            for i, item in enumerate(st.session_state.history[1:], 1):
                with st.expander(f"[{item['time']}] {item['ticker']} ({item.get('model', '')})", expanded=False):
                    st.markdown(item['result'])

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

            # ★ 거래량 0 감지 → 프리마켓/애프터마켓 안내
            vol_warning = ""
            if d1['Volume'] == 0 or d5['Volume'] == 0:
                vol_warning = "\n※ 현재 프리마켓/애프터마켓 시간대로 거래량 데이터가 제공되지 않습니다. 거래량 분석은 제외하고 가격 기반으로만 전략을 세워줘."
                st.session_state.volume_unavailable = True
            else:
                st.session_state.volume_unavailable = False

            # ★ 장 마감 상태 체크
            st.session_state.market_closed = not is_market_open()

            prompt = f"""
            너는 미국 주식 전문 트레이더야. [{ticker}]의 데이터를 보고 일 3% 수익 목표 단타 전략을 세워줘.

            [1분봉] 가격: {d1['Close']:.2f}, 거래량: {d1['Volume']:,.0f}, 5이평: {d1['SMA_5']:.2f}, 20이평: {d1['SMA_20']:.2f}, 스토캐스틱K: {d1['Stoch_K']:.2f}
            [5분봉] 가격: {d5['Close']:.2f}, 거래량: {d5['Volume']:,.0f}, CCI: {d5['CCI']:.2f}
            {line_30m}
            {vol_warning}

            분석 요구사항:
            1. 거래량 추이: 현재 변동성이 유의미한 거래량을 동반한 진짜 움직임인지 분석해줘.
            2. 전략 제안: 구체적인 진입가, 목표가(3% 수익), 손절가를 제안해줘.
            """

            try:
                # ★ 모델 폴백: Pro → Flash → 소진 메시지
                # Pro가 이미 소진된 경우 바로 Flash로 건너뜀 (불필요한 API 호출 0)
                MODELS = [
                    ("gemini-2.5-pro", "Pro"),
                    ("gemini-2.5-flash", "Flash"),
                ]
                response = None
                used_model = ""
                
                for model_id, model_label in MODELS:
                    # ★ Pro가 전역적으로 소진된 상태면 스킵
                    if model_label == "Pro" and tracker["pro_exhausted"]:
                        continue

                    try:
                        # 분당 제한(429) / 서버 과부하(503) 대비 최대 2회 재시도
                        for attempt in range(2):
                            try:
                                response = client.models.generate_content(model=model_id, contents=prompt)
                                used_model = model_label
                                break
                            except Exception as api_err:
                                err_str = str(api_err)
                                if "PerDay" in err_str or "daily" in err_str.lower():
                                    if model_label == "Pro":
                                        tracker["pro_exhausted"] = True  # ★ 전역 플래그 저장
                                    raise api_err  # 일일 소진 → 다음 모델로
                                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                                        or "503" in err_str or "UNAVAILABLE" in err_str):
                                    if attempt < 1:
                                        time.sleep(15)
                                        continue
                                raise api_err
                        if response:
                            break  # 성공 시 모델 루프 탈출
                    except Exception:
                        continue  # 이 모델 실패 → 다음 모델 시도
                
                if response:
                    st.session_state.analysis_result = response.text
                    st.session_state.last_ticker = ticker
                    st.session_state.used_model = used_model
                    tracker["last_run_time"] = time.time()
                    # ★ 히스토리 저장 (최대 10개, 오래된 것 자동 삭제)
                    from datetime import datetime
                    st.session_state.history.insert(0, {
                        "ticker": ticker,
                        "result": response.text,
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "model": used_model,
                    })
                    if len(st.session_state.history) > 10:
                        st.session_state.history.pop()
                else:
                    st.session_state.error_message = "🚫 금일 무료 토큰이 모두 소진되었습니다. 내일 접속하세요."

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
