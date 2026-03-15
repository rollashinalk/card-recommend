# 일본 결제 카드 추천 (Streamlit)

Streamlit Community Cloud에 바로 배포할 수 있는 최소 카드 추천 앱입니다.

## 포함 파일
- `app.py`: 메인 Streamlit 앱
- `requirements.txt`: 배포 의존성

## 기능
- 실시간 USD/JPY 환율 조회
- 결제 금액(JPY) 입력 후 카드별 행사 비교
- USD 기준 / JPY 기준 행사 동시 계산
- 모바일에서 보기 쉬운 단순 UI
- 카드/행사 테이블 직접 수정 가능

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud 배포
1. GitHub 저장소 연결
2. Main file path를 `app.py`로 설정
3. Deploy

