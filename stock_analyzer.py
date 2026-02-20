import streamlit as st
import yfinance as yf
import pandas as pd
import pandas_ta as ta
from google import genai

# 1. 페이지 설정
st.set_page_config(page_title="단타 분석기", page_icon="📈", layout="centered")

# 2. API 키 설정 (사용자 제공 키 적용)
GEMINI_API_KEY = st.secrets["GEMINI_KEY"]
client = genai.Client(api_key=GEMINI_API_KEY)

# 3. 데이터 수집 및 보조지표 계산 함수
def get_stock_data(ticker, interval):
    # 기간 설정 (분봉은 최근 5일 데이터면 충분)
    period = "5d" if "m" in interval else "1mo"
    
    try:
        df = yf.download(ticker, period=period, interval=interval, prepost=True, progress=False)
        if df is None or df.empty:
            return None
            
        # 데이터 클리닝
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
            
        # 보조지표 계산
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
            df['Stoch_D'] = stoch.iloc[:, 1]
            
        return df.iloc[-1] # 가장 최신행 반환
    except:
        return None

# 4. 웹 UI 구성
st.title("📈 AI 단타 분석기 (V2.5)")
st.write("실시간 분봉 지표를 분석하여 최적의 매매 전략을 도출합니다.")

# 입력창
ticker = st.text_input("분석할 티커(Ticker)를 입력하세요", value="SOXL").upper()

if st.button("제미니 AI 분석 시작"):
  # 1. 상태 표시 컨테이너 생성 (st.spinner 대신 st.status 사용)
    with st.status(f"[{ticker}] 유효성을 확인 중...", expanded=False) as status:
        # 데이터 존재 여부 확인
        check_df = yf.download(ticker, period="1d", progress=False)
        
        if check_df.empty:
            # 실패 시: 문구 수정 + 상태를 'error'로 변경 (아이콘이 ❌로 바뀜)
            status.update(label=f"[{ticker}] 유효성 확인 완료 - 읽기 실패", state="error", expanded=False)
            st.error(f"'{ticker}'는 존재하지 않거나 상장 폐지된 종목입니다. 티커를 확인해 주세요.")
            st.stop() # 여기서 즉시 중단
            
        # 성공 시: 문구 수정 + 상태를 'complete'로 변경 (아이콘이 ✅로 바뀜)
        status.update(label=f"[{ticker}] 유효성 확인 완료", state="complete", expanded=False)
    
    
    # 2. 유효성 검사 통과 후 분석
    with st.spinner(f"[{ticker}] 데이터를 분석 중입니다..."):
        # 데이터 수집 (1분, 5분, 30분봉)
        d1 = get_stock_data(ticker, "1m")
        d5 = get_stock_data(ticker, "5m")
        d30 = get_stock_data(ticker, "30m")
        
        if d1 is not None and d5 is not None:
            # 프롬프트 생성
            prompt = f"""
            너는 미국 주식 전문 트레이더야. [{ticker}]의 데이터를 보고 일 3% 목표 단타 전략을 세워줘.
            
            [1분봉] 현재가: {d1['Close']:.2f}, 5이평: {d1['SMA_5']:.2f}, 20이평: {d1['SMA_20']:.2f}, 스토캐스틱K: {d1['Stoch_K']:.2f}
            [5분봉] CCI: {d5['CCI']:.2f}, 종가: {d5['Close']:.2f}
            [30분봉] 20이평: {d30['SMA_20']:.2f}
            
            분석 사항:
            1. 현재 차트 요약
            2. 매수 진입가, 목표가, 손절가 제안
            """
            
            try:
                # 제미니 2.5 Flash 호출
                response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                
                st.success("분석 완료!")
                st.markdown("---")
                st.markdown(response.text)
                
            except Exception as e:
                st.error(f"분석 중 오류 발생: {e}")
        else:
            st.error("데이터를 가져올 수 없습니다. 티커를 다시 확인하거나 장 시간을 확인해주세요.")

st.caption("※ 이 분석은 투자 참고용이며, 모든 투자의 책임은 본인에게 있습니다.")