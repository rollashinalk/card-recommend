# 일본 결제 카드 추천 (Streamlit)

Streamlit Community Cloud에 바로 배포할 수 있는 카드 추천 앱입니다.

## 포함 파일
- `app.py`: 메인 Streamlit 앱
- `requirements.txt`: 배포 의존성
- `promotions_template.csv`: 최신 카드행사 템플릿

## 현재 기능
- 실시간 환율 조회 (USD/JPY/KRW)
- 결제 금액(JPY, 정수) 입력
- 가맹점 유형 버튼 2개 제공
  - `일반`
  - `KB 3대 편의점(세븐, 로손, 패밀리)`
- 리워드 타입 지원
  - `percent_discount`
  - `fixed_cashback`
  - `cashback_with_cap`
  - `formula_cashback`
- 카드/행사 테이블 직접 수정 + CSV 업로드

## 프로모션 CSV 컬럼
`card_name,enabled,reward_type,start_date,end_date,min_amount,min_currency,percent_value,fixed_amount,max_reward_per_txn,max_reward_per_txn_currency,max_uses,used_count,total_cap_amount,total_cap_currency,total_used_amount,merchant_type,formula_id,formula_params_json`

## 참고
- `merchant_type=kb_cvs3`는 KB 편의점 그룹(세븐/로손/패밀리) 전용입니다.
- `max_uses`가 비어있거나 0이면 횟수 제한 없음으로 처리됩니다.
- BC GOAT처럼 KRW 한도형 규칙도 CSV로 입력 가능합니다.
- 신한 더모아(`formula_cashback`)는 현재 샘플 공식(`shinhan_the_more_v1`) 기반으로 동작하며, 추후 공식 엔진 확장이 가능합니다.

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud 배포
1. GitHub 저장소 연결
2. Main file path를 `app.py`로 설정
3. Deploy
