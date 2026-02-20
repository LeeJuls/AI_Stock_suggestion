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

# 6. 분석 실행 함수 (콜백에서 호출)
def start_analysis():
    st.session_state.is_running = True

# 7. 웹 UI 구성
st.title("📈 AI 단타 분석기 (V3.2)")
st.write("실시간 지표와 거래량을 분석하여 정밀한 매매 전략을 도출합니다.")

ticker = st.text_input("분석할 미장 티커(Ticker)를 입력하세요", value="SOXL").upper()

# --- 쿨타임 및 버튼 제어 로직 ---
current_time = time.time()
elapsed = current_time - tracker["last_run_time"]
remaining = int(COOLDOWN_LIMIT - elapsed)

if remaining > 0:
    # 쿨타임 중: 버튼 비활성화
    st.button("제미니 AI 분석 시작", disabled=True, key="wait_btn")
    st.info(f"⏳ 글로벌 쿨타임 중입니다. **약 {remaining}초** 후 페이지를 새로고침해주세요.")

elif st.session_state.is_running:
    # ★ 분석 진행 중: 버튼 비활성화 + 스피너 표시
    st.button("분석 엔진 가동 중...", disabled=True, key="running_btn")
    
    tracker["last_run_time"] = time.time()
    
    with st.spinner(f"[{ticker}] 상세 지표 및 거래량 분석 중..."):
        d1 = get_stock_data(ticker, "1m")
        
        if d1 is None:
            st.error(f"'{ticker}'의 데이터를 가져올 수 없습니다. 티커가 유효한지, 장 중인지 확인해주세요.")
            st.session_state.is_running = False
            st.stop()
        
        d5 = get_stock_data(ticker, "5m")
        d30 = get_stock_data(ticker, "30m")
        
        if d5 is None:
            st.error(f"'{ticker}'의 5분봉 데이터를 가져올 수 없습니다. 잠시 후 다시 시도해주세요.")
            st.session_state.is_running = False
            st.stop()
        
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
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            st.session_state.analysis_result = response.text
            st.session_state.last_ticker = ticker
        except Exception as e:
            st.error(f"AI 분석 중 오류가 발생했습니다: {e}")
        
        # 분석 완료 → 플래그 해제 후 rerun
        st.session_state.is_running = False
        st.rerun()

else:
    # ★ 대기 상태: on_click 콜백으로 클릭 즉시 is_running=True → rerun 시 버튼 비활성화
    st.button("제미니 AI 분석 시작", key="start_btn", on_click=start_analysis)

# 세션에 저장된 분석 결과가 있으면 항상 표시
if st.session_state.analysis_result:
    st.divider()
    st.success(f"[{st.session_state.last_ticker}] 분석 결과")
    st.markdown(st.session_state.analysis_result)

st.caption("※ 이 분석은 투자 참고용이며, 모든 투자의 책임은 투자자 본인에게 있습니다.")