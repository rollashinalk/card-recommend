import csv
import datetime as dt
import io
from dataclasses import dataclass
from typing import List

import requests
import streamlit as st

st.set_page_config(page_title="일본 카드 추천", page_icon="💳", layout="centered")

MERCHANT_NORMAL = "일반"
MERCHANT_KB_CVS3 = "KB 3대 편의점(세븐, 로손, 패밀리)"
SUPPORTED_CURRENCIES = ["JPY", "USD", "KRW"]


@dataclass
class CardPromo:
    card_name: str
    enabled: bool
    reward_type: str
    start_date: dt.date | None
    end_date: dt.date | None
    min_amount: float
    min_currency: str
    percent_value: float
    fixed_amount: float
    max_reward_per_txn: float
    max_reward_per_txn_currency: str
    max_uses: int
    total_cap_amount: float
    total_cap_currency: str
    monthly_cashback_cap_amount: float
    monthly_cashback_cap_currency: str
    monthly_eligible_spend_cap_amount: float
    monthly_eligible_spend_cap_currency: str
    merchant_type: str
    formula_id: str
    formula_params_json: str


@dataclass
class Transaction:
    txn_id: str
    txn_date: dt.date
    card_name: str
    amount: float
    currency: str
    merchant_type: str
    status: str
    note: str


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    return dt.date.fromisoformat(value)


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_float_or_zero(value) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    return float(text)


def parse_int_or_zero(value) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    return int(float(text))


def normalize_status(value: str) -> str:
    status = str(value or "").strip().lower()
    if status == "cancelled":
        return "cancelled"
    return "approved"


def get_fx_rates() -> dict[str, float] | None:
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        response.raise_for_status()
        data = response.json()
        if data.get("result") == "success":
            rates = data.get("rates", {})
            if "JPY" in rates and "KRW" in rates:
                return {"USD": 1.0, "JPY": float(rates["JPY"]), "KRW": float(rates["KRW"])}
    except Exception:
        return None
    return None


def convert(amount: float, from_currency: str, to_currency: str, fx_rates: dict[str, float]) -> float:
    if from_currency == to_currency:
        return amount
    if from_currency not in fx_rates or to_currency not in fx_rates:
        raise ValueError("Unsupported currency conversion")

    usd_amount = amount / fx_rates[from_currency]
    return usd_amount * fx_rates[to_currency]


def merchant_match(promo_merchant_type: str, selected_merchant_type: str) -> bool:
    if promo_merchant_type == "all":
        return True
    if promo_merchant_type == "kb_cvs3" and selected_merchant_type == MERCHANT_KB_CVS3:
        return True
    return False


def calc_formula_cashback(pay_krw: int, formula_id: str, formula_params_json: str) -> float:
    if formula_id != "shinhan_the_more_v1":
        return 0.0

    return float((pay_krw % 1000) * 2)


def recalc_promo_usage(
    promo: CardPromo,
    transactions: List[Transaction],
    fx_rates: dict[str, float],
) -> tuple[int, float]:
    used_count = 0
    total_used_amount = 0.0
    for txn in transactions:
        if txn.status != "approved":
            continue
        if txn.card_name != promo.card_name:
            continue
        txn_date = txn.txn_date
        if promo.start_date and txn_date < promo.start_date:
            continue
        if promo.end_date and txn_date > promo.end_date:
            continue
        if not merchant_match(promo.merchant_type, txn.merchant_type):
            continue

        txn_amount_in_min_currency = convert(txn.amount, txn.currency, promo.min_currency, fx_rates)
        if txn_amount_in_min_currency < promo.min_amount:
            continue

        used_count += 1
        total_used_amount += convert(txn.amount, txn.currency, promo.total_cap_currency, fx_rates)

    return used_count, total_used_amount


def recalc_monthly_usage(
    promo: CardPromo,
    pay_date: dt.date,
    transactions: List[Transaction],
    fx_rates: dict[str, float],
) -> tuple[float, float]:
    monthly_used_spend_jpy = 0.0
    monthly_used_cashback_jpy = 0.0
    target_year_month = pay_date.strftime("%Y-%m")

    monthly_approved_txns = [
        txn
        for txn in transactions
        if txn.status == "approved"
        and txn.card_name == promo.card_name
        and txn.txn_date.strftime("%Y-%m") == target_year_month
        and txn.txn_date <= pay_date
        and (not promo.start_date or txn.txn_date >= promo.start_date)
        and (not promo.end_date or txn.txn_date <= promo.end_date)
        and merchant_match(promo.merchant_type, txn.merchant_type)
    ]
    monthly_approved_txns.sort(key=lambda txn: (txn.txn_date, txn.txn_id))

    for txn in monthly_approved_txns:
        txn_amount_min_currency = convert(txn.amount, txn.currency, promo.min_currency, fx_rates)
        if txn_amount_min_currency < promo.min_amount:
            continue

        txn_amount_jpy = convert(txn.amount, txn.currency, "JPY", fx_rates)
        eligible_spend_jpy = txn_amount_jpy

        if promo.monthly_eligible_spend_cap_amount > 0:
            monthly_spend_cap_jpy = convert(
                promo.monthly_eligible_spend_cap_amount,
                promo.monthly_eligible_spend_cap_currency,
                "JPY",
                fx_rates,
            )
            monthly_spend_remaining = max(monthly_spend_cap_jpy - monthly_used_spend_jpy, 0)
            if monthly_spend_remaining <= 0:
                continue
            eligible_spend_jpy = min(eligible_spend_jpy, monthly_spend_remaining)

        monthly_used_spend_jpy += eligible_spend_jpy

        if promo.reward_type != "cashback_with_cap":
            continue

        reward_jpy = eligible_spend_jpy * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            per_txn_cap_jpy = convert(
                promo.max_reward_per_txn,
                promo.max_reward_per_txn_currency,
                "JPY",
                fx_rates,
            )
            reward_jpy = min(reward_jpy, per_txn_cap_jpy)

        if promo.monthly_cashback_cap_amount > 0:
            monthly_cashback_cap_jpy = convert(
                promo.monthly_cashback_cap_amount,
                promo.monthly_cashback_cap_currency,
                "JPY",
                fx_rates,
            )
            monthly_cashback_remaining = max(monthly_cashback_cap_jpy - monthly_used_cashback_jpy, 0)
            if monthly_cashback_remaining <= 0:
                continue
            reward_jpy = min(reward_jpy, monthly_cashback_remaining)

        monthly_used_cashback_jpy += max(reward_jpy, 0)

    return monthly_used_spend_jpy, monthly_used_cashback_jpy


def evaluate(
    promo: CardPromo,
    pay_jpy: int,
    pay_date: dt.date,
    merchant_type: str,
    fx_rates: dict[str, float],
    transactions: List[Transaction],
) -> tuple[int, str]:
    used_count, total_used_amount = recalc_promo_usage(promo, transactions, fx_rates)

    if not promo.enabled:
        return 0, "카드 비활성화"
    if promo.start_date and pay_date < promo.start_date:
        return 0, "행사 시작 전"
    if promo.end_date and pay_date > promo.end_date:
        return 0, "행사 종료 후"
    if not merchant_match(promo.merchant_type, merchant_type):
        return 0, "가맹점 조건 불일치"
    if promo.max_uses > 0 and used_count >= promo.max_uses:
        return 0, "사용 횟수 소진"

    pay_in_min_currency = convert(float(pay_jpy), "JPY", promo.min_currency, fx_rates)
    if pay_in_min_currency < promo.min_amount:
        return 0, f"최소 결제 금액 미달 ({promo.min_amount:g} {promo.min_currency})"

    reward_jpy = 0.0
    reason = ""

    if promo.reward_type == "percent_discount":
        raw = pay_jpy * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            per_txn_cap_jpy = convert(
                promo.max_reward_per_txn,
                promo.max_reward_per_txn_currency,
                "JPY",
                fx_rates,
            )
            raw = min(raw, per_txn_cap_jpy)
        reward_jpy = raw
        reason = f"정률 할인 {promo.percent_value:g}%"

    elif promo.reward_type == "fixed_cashback":
        reward_jpy = convert(promo.fixed_amount, promo.min_currency, "JPY", fx_rates)
        reason = f"정액 캐시백 {promo.fixed_amount:g} {promo.min_currency}"

    elif promo.reward_type == "cashback_with_cap":
        # 1) 건별 리워드 계산
        raw = pay_jpy * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            per_txn_cap_jpy = convert(
                promo.max_reward_per_txn,
                promo.max_reward_per_txn_currency,
                "JPY",
                fx_rates,
            )
            raw = min(raw, per_txn_cap_jpy)

        # 2) 월 결제금액(적립 대상 사용액) 잔여 한도 반영
        reason = f"캐시백 {promo.percent_value:g}% + 한도 적용"
        reward_jpy = raw
        monthly_used_spend_jpy, monthly_used_cashback_jpy = recalc_monthly_usage(
            promo,
            pay_date,
            transactions,
            fx_rates,
        )
        if promo.monthly_eligible_spend_cap_amount > 0:
            monthly_spend_cap_jpy = convert(
                promo.monthly_eligible_spend_cap_amount,
                promo.monthly_eligible_spend_cap_currency,
                "JPY",
                fx_rates,
            )
            monthly_spend_remaining = max(monthly_spend_cap_jpy - monthly_used_spend_jpy, 0)
            if monthly_spend_remaining <= 0:
                return 0, "월 적립 대상 사용액 한도 소진"

            eligible_spend_for_txn_jpy = min(float(pay_jpy), monthly_spend_remaining)
            reward_jpy = reward_jpy * (eligible_spend_for_txn_jpy / float(pay_jpy))
            reason += f" · 월 적립대상 잔여 ¥{monthly_spend_remaining:,.0f}"

        # 3) 월 캐시백 잔여 한도 반영
        if promo.monthly_cashback_cap_amount > 0:
            monthly_cashback_cap_jpy = convert(
                promo.monthly_cashback_cap_amount,
                promo.monthly_cashback_cap_currency,
                "JPY",
                fx_rates,
            )
            monthly_cashback_remaining = max(monthly_cashback_cap_jpy - monthly_used_cashback_jpy, 0)
            if monthly_cashback_remaining <= 0:
                return 0, "월 캐시백 한도 소진"

            reward_jpy = min(reward_jpy, monthly_cashback_remaining)
            reason += f" · 월 캐시백 잔여 ¥{monthly_cashback_remaining:,.0f}"

    elif promo.reward_type == "formula_cashback":
        pay_krw = int(round(convert(float(pay_jpy), "JPY", "KRW", fx_rates)))
        reward_krw = calc_formula_cashback(pay_krw, promo.formula_id, promo.formula_params_json)
        reward_jpy = convert(reward_krw, "KRW", "JPY", fx_rates)
        reason = f"공식 캐시백 ({promo.formula_id})"

    else:
        return 0, "지원하지 않는 리워드 타입"

    if promo.total_cap_amount > 0:
        total_cap_jpy = convert(promo.total_cap_amount, promo.total_cap_currency, "JPY", fx_rates)
        total_used_jpy = convert(total_used_amount, promo.total_cap_currency, "JPY", fx_rates)
        remaining = max(total_cap_jpy - total_used_jpy, 0)
        if remaining <= 0:
            return 0, "총 한도 소진"
        reward_jpy = min(reward_jpy, remaining)
        reason += f" · 잔여 한도 ¥{remaining:,.0f}"

    return int(round(max(reward_jpy, 0))), reason


def seed_promotions() -> List[CardPromo]:
    return [
        CardPromo("KB UPI (가온 체크)", True, "percent_discount", dt.date(2026, 2, 14), dt.date(2026, 5, 13), 10000, "JPY", 15, 0, 2000, "JPY", 5, 0, "JPY", 0, "JPY", 0, "JPY", "all", "", ""),
        CardPromo("하나 UPI (트래블로그)", True, "percent_discount", dt.date(2026, 2, 11), dt.date(2026, 4, 30), 50, "USD", 20, 0, 10, "USD", 3, 0, "JPY", 0, "JPY", 0, "JPY", "all", "", ""),
        CardPromo("우리 UPI (SKT우리)", True, "percent_discount", dt.date(2025, 12, 22), dt.date(2026, 5, 31), 50, "USD", 11, 0, 15, "USD", 3, 0, "JPY", 0, "JPY", 0, "JPY", "all", "", ""),
        CardPromo("BC GOAT", True, "cashback_with_cap", None, None, 0, "USD", 6, 0, 0, "USD", 0, 30000, "KRW", 10000, "KRW", 200000, "KRW", "all", "", ""),
        CardPromo("KB 일본 편의점 행사 (KB 트래블러스)", True, "fixed_cashback", dt.date(2026, 3, 1), dt.date(2026, 3, 31), 1000, "JPY", 0, 500, 0, "JPY", 10, 5000, "JPY", 0, "JPY", 0, "JPY", "kb_cvs3", "", ""),
        CardPromo("신한 더모아", True, "formula_cashback", None, None, 0, "KRW", 0, 0, 0, "KRW", 0, 0, "KRW", 0, "KRW", 0, "KRW", "all", "shinhan_the_more_v1", ""),
    ]


def seed_transactions() -> List[Transaction]:
    return [
        Transaction("txn-001", dt.date(2026, 3, 2), "KB UPI (가온 체크)", 12000, "JPY", "all", "approved", "정상 승인"),
        Transaction("txn-002", dt.date(2026, 3, 5), "KB UPI (가온 체크)", 13000, "JPY", "all", "cancelled", "오입력 취소"),
    ]


def promo_rows(promos: List[CardPromo]) -> List[dict]:
    return [p.__dict__ for p in promos]


def rows_to_promos(rows: List[dict]) -> List[CardPromo]:
    promos = []
    for row in rows:
        try:
            promos.append(
                CardPromo(
                    card_name=str(row.get("card_name", "")),
                    enabled=bool(row.get("enabled", True)),
                    reward_type=str(row.get("reward_type", "percent_discount")),
                    start_date=row.get("start_date"),
                    end_date=row.get("end_date"),
                    min_amount=parse_float_or_zero(row.get("min_amount", 0)),
                    min_currency=str(row.get("min_currency", "JPY")),
                    percent_value=parse_float_or_zero(row.get("percent_value", 0)),
                    fixed_amount=parse_float_or_zero(row.get("fixed_amount", 0)),
                    max_reward_per_txn=parse_float_or_zero(row.get("max_reward_per_txn", 0)),
                    max_reward_per_txn_currency=str(row.get("max_reward_per_txn_currency", "JPY")),
                    max_uses=parse_int_or_zero(row.get("max_uses", 0)),
                    total_cap_amount=parse_float_or_zero(row.get("total_cap_amount", 0)),
                    total_cap_currency=str(row.get("total_cap_currency", "JPY")),
                    monthly_cashback_cap_amount=parse_float_or_zero(row.get("monthly_cashback_cap_amount", 0)),
                    monthly_cashback_cap_currency=str(row.get("monthly_cashback_cap_currency", "JPY")),
                    monthly_eligible_spend_cap_amount=parse_float_or_zero(row.get("monthly_eligible_spend_cap_amount", 0)),
                    monthly_eligible_spend_cap_currency=str(row.get("monthly_eligible_spend_cap_currency", "JPY")),
                    merchant_type=str(row.get("merchant_type", "all")),
                    formula_id=str(row.get("formula_id", "")),
                    formula_params_json=str(row.get("formula_params_json", "")),
                )
            )
        except Exception:
            continue
    return promos


def transaction_rows(transactions: List[Transaction]) -> List[dict]:
    rows = []
    for txn in transactions:
        rows.append(
            {
                "txn_id": txn.txn_id,
                "txn_date": txn.txn_date,
                "card_name": txn.card_name,
                "amount": txn.amount,
                "currency": txn.currency,
                "merchant_type": txn.merchant_type,
                "status": txn.status,
                "note": txn.note,
            }
        )
    return rows


def rows_to_transactions(rows: List[dict]) -> List[Transaction]:
    transactions = []
    for row in rows:
        try:
            transactions.append(
                Transaction(
                    txn_id=str(row.get("txn_id", "")).strip(),
                    txn_date=row.get("txn_date"),
                    card_name=str(row.get("card_name", "")),
                    amount=float(row.get("amount", 0)),
                    currency=str(row.get("currency", "JPY")),
                    merchant_type=str(row.get("merchant_type", "all")),
                    status=normalize_status(str(row.get("status", "approved"))),
                    note=str(row.get("note", "")),
                )
            )
        except Exception:
            continue
    return transactions


def load_promos_from_csv(uploaded_file) -> List[CardPromo]:
    text = uploaded_file.getvalue().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        rows.append(
            {
                "card_name": r.get("card_name", ""),
                "enabled": parse_bool(r.get("enabled", "true")),
                "reward_type": r.get("reward_type", "percent_discount"),
                "start_date": parse_date(r.get("start_date", "")),
                "end_date": parse_date(r.get("end_date", "")),
                "min_amount": parse_float_or_zero(r.get("min_amount", 0)),
                "min_currency": r.get("min_currency", "JPY") or "JPY",
                "percent_value": parse_float_or_zero(r.get("percent_value", 0)),
                "fixed_amount": parse_float_or_zero(r.get("fixed_amount", 0)),
                "max_reward_per_txn": parse_float_or_zero(r.get("max_reward_per_txn", 0)),
                "max_reward_per_txn_currency": r.get("max_reward_per_txn_currency", "JPY") or "JPY",
                "max_uses": parse_int_or_zero(r.get("max_uses", 0)),
                "total_cap_amount": parse_float_or_zero(r.get("total_cap_amount", 0)),
                "total_cap_currency": r.get("total_cap_currency", "JPY") or "JPY",
                "monthly_cashback_cap_amount": parse_float_or_zero(r.get("monthly_cashback_cap_amount", 0)),
                "monthly_cashback_cap_currency": r.get("monthly_cashback_cap_currency", "JPY") or "JPY",
                "monthly_eligible_spend_cap_amount": parse_float_or_zero(r.get("monthly_eligible_spend_cap_amount", 0)),
                "monthly_eligible_spend_cap_currency": r.get("monthly_eligible_spend_cap_currency", "JPY") or "JPY",
                "merchant_type": r.get("merchant_type", "all") or "all",
                "formula_id": r.get("formula_id", "") or "",
                "formula_params_json": r.get("formula_params_json", "") or "",
            }
        )
    return rows_to_promos(rows)


st.title("💳 일본 결제 카드 추천")
st.caption("JPY 결제 금액(정수)과 가맹점 버튼을 선택하면, 카드 행사 조건을 비교해 추천합니다.")

if "promos" not in st.session_state:
    st.session_state.promos = seed_promotions()
if "transactions" not in st.session_state:
    st.session_state.transactions = seed_transactions()
if "selected_merchant_type" not in st.session_state:
    st.session_state.selected_merchant_type = MERCHANT_NORMAL

with st.container(border=True):
    left, right = st.columns([2, 1])
    with left:
        st.subheader("실시간 환율")
    with right:
        refresh = st.button("환율 새로고침", use_container_width=True)

    if refresh or "fx_rates" not in st.session_state:
        st.session_state.fx_rates = get_fx_rates()

    fx_rates = st.session_state.fx_rates
    if fx_rates:
        st.success(f"1 USD = {fx_rates['JPY']:,.2f} JPY | 1 USD = {fx_rates['KRW']:,.2f} KRW")
    else:
        st.error("환율 조회 실패. 잠시 후 다시 시도해 주세요.")

with st.container(border=True):
    st.subheader("결제 정보")
    st.write("가맹점 유형")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("일반", use_container_width=True, type="primary" if st.session_state.selected_merchant_type == MERCHANT_NORMAL else "secondary"):
            st.session_state.selected_merchant_type = MERCHANT_NORMAL
    with col_b:
        if st.button("KB 3대 편의점(세븐, 로손, 패밀리)", use_container_width=True, type="primary" if st.session_state.selected_merchant_type == MERCHANT_KB_CVS3 else "secondary"):
            st.session_state.selected_merchant_type = MERCHANT_KB_CVS3

    merchant_type = st.session_state.selected_merchant_type
    pay_jpy = st.number_input("결제 금액 (JPY)", min_value=0, step=1000, value=12000, format="%d")
    pay_date = st.date_input("결제 날짜", value=dt.date.today())

with st.container(border=True):
    st.subheader("카드/행사 목록")
    st.caption("CSV 업로드 또는 테이블 직접 수정으로 행사 정보를 관리할 수 있습니다.")
    uploaded_csv = st.file_uploader("CSV 업로드", type=["csv"])
    if uploaded_csv is not None:
        try:
            st.session_state.promos = load_promos_from_csv(uploaded_csv)
            st.success("CSV를 불러왔습니다.")
        except Exception as exc:
            st.error(f"CSV 파싱 실패: {exc}")

    edited = st.data_editor(
        promo_rows(st.session_state.promos),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "reward_type": st.column_config.SelectboxColumn(options=["percent_discount", "fixed_cashback", "cashback_with_cap", "formula_cashback"]),
            "min_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "max_reward_per_txn_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "total_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "monthly_cashback_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "monthly_eligible_spend_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "merchant_type": st.column_config.SelectboxColumn(options=["all", "kb_cvs3"]),
            "formula_params_json": st.column_config.TextColumn(help="선택 입력(현재 shinhan_the_more_v1은 파라미터 미사용)"),
        },
    )
    st.session_state.promos = rows_to_promos(edited)

with st.container(border=True):
    st.subheader("결제내역 관리")
    st.caption("행 추가/수정 후 status를 변경해 관리하세요. 사용자 실수 복구를 위해 삭제 대신 status=cancelled 사용을 권장합니다.")
    edited_txns = st.data_editor(
        transaction_rows(st.session_state.transactions),
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "txn_id": st.column_config.TextColumn(help="거래 식별자(중복 허용, 추적용 권장)"),
            "txn_date": st.column_config.DateColumn(format="YYYY-MM-DD", required=True),
            "currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
            "merchant_type": st.column_config.SelectboxColumn(options=["all", "kb_cvs3"]),
            "status": st.column_config.SelectboxColumn(options=["approved", "cancelled"]),
            "note": st.column_config.TextColumn(help="취소 사유/메모"),
        },
    )
    st.session_state.transactions = rows_to_transactions(edited_txns)

if st.button("최적 카드 계산", type="primary", use_container_width=True):
    if not fx_rates:
        st.warning("환율을 불러온 뒤 다시 계산해 주세요.")
    elif pay_jpy <= 0:
        st.warning("결제 금액을 0보다 크게 입력해 주세요.")
    else:
        results = []
        for promo in st.session_state.promos:
            reward_jpy, reason = evaluate(
                promo,
                int(pay_jpy),
                pay_date,
                merchant_type,
                fx_rates,
                st.session_state.transactions,
            )
            reward_usd = convert(float(reward_jpy), "JPY", "USD", fx_rates)
            results.append({"card_name": promo.card_name, "reward_jpy": reward_jpy, "reward_usd": reward_usd, "reason": reason})

        results.sort(key=lambda x: x["reward_jpy"], reverse=True)
        if not results:
            st.info("등록된 카드 행사가 없습니다.")
        else:
            best = results[0]
            st.success(f"추천 카드: {best['card_name']} | 예상 혜택 ¥{best['reward_jpy']:,.0f} (약 ${best['reward_usd']:,.2f})")
            for i, row in enumerate(results, start=1):
                with st.container(border=True):
                    st.markdown(f"**{i}위. {row['card_name']}**")
                    st.write(f"예상 혜택: ¥{row['reward_jpy']:,.0f} (약 ${row['reward_usd']:,.2f})")
                    st.caption(f"근거: {row['reason']}")

st.caption("배포용 참고: Streamlit Community Cloud에서 main 파일을 app.py로 지정하세요.")
