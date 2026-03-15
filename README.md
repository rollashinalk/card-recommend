# 일본 결제 카드 추천 (Streamlit)

Streamlit Community Cloud에 바로 배포할 수 있는 카드 추천 앱입니다.

## 포함 파일
- `app.py`: 메인 Streamlit 앱
- `requirements.txt`: 배포 의존성

## 현재 기능
- 실시간 USD/JPY 환율 조회
- 결제 금액(JPY, 정수) 입력
- 가맹점 유형 버튼 2개 제공
  - `일반` 버튼
  - `KB 3대 편의점(세븐, 로손, 패밀리)` 버튼
- 리워드 타입 지원
  - `percent_discount`
  - `fixed_cashback`
  - `cashback_with_cap`
  - `formula_cashback`
- 카드/행사 테이블 직접 수정 가능

## 프로모션 스키마(테이블/CSV 호환)
권장 컬럼:
- `card_name`, `enabled`, `reward_type`
- `start_date`, `end_date` (비워두면 상시)
- `min_amount`, `min_currency`
- `percent_value`, `fixed_amount`
- `max_reward_per_txn`, `max_reward_per_txn_currency`
- `max_uses`, `used_count`
- `total_cap_amount`, `total_cap_currency`, `total_used_amount`
- `merchant_type` (`all` 또는 `kb_cvs3`)
- `formula_id`, `formula_params_json`

## KB Travelers 예시 규칙
- 기간: 2026-03-01 ~ 2026-03-31
- 최소 결제: JPY 1,000
- 혜택: 건당 JPY 500 정액 캐시백
- 최대 횟수: 10회
- 총 한도: JPY 5,000
- 가맹점: `kb_cvs3`

## 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Community Cloud 배포
1. GitHub 저장소 연결
2. Main file path를 `app.py`로 설정
3. Deploy
