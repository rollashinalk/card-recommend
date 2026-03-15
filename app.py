import csv
import datetime as dt
import io
import json
from dataclasses import dataclass
from typing import List

import requests
import streamlit as st

st.set_page_config(page_title="일본 카드 추천", page_icon="💳", layout="centered")

MERCHANT_NORMAL = "일반"
MERCHANT_KB_CVS3 = "KB 3대 편의점(세븐, 로손, 패밀리)"


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
    used_count: int
    total_cap_amount: float
    total_cap_currency: str
    total_used_amount: float
    merchant_type: str
    formula_id: str
    formula_params_json: str


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    return dt.date.fromisoformat(value)


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def get_usd_jpy_rate() -> float | None:
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        response.raise_for_status()
        data = response.json()
        if data.get("result") == "success":
            return float(data["rates"]["JPY"])
    except Exception:
        return None
    return None


def convert(amount: float, from_currency: str, to_currency: str, usd_jpy: float) -> float:
    if from_currency == to_currency:
        return amount
    if from_currency == "USD" and to_currency == "JPY":
        return amount * usd_jpy
    if from_currency == "JPY" and to_currency == "USD":
        return amount / usd_jpy
    raise ValueError("Unsupported currency conversion")


def merchant_match(promo_merchant_type: str, selected_merchant_type: str) -> bool:
    if promo_merchant_type == "all":
        return True
    if promo_merchant_type == "kb_cvs3" and selected_merchant_type == MERCHANT_KB_CVS3:
        return True
    return False


def calc_formula_cashback(pay_jpy: int, formula_id: str, formula_params_json: str) -> float:
    if formula_id != "shinhan_the_more_v1":
        return 0.0

    try:
        params = json.loads(formula_params_json or "{}")
    except Exception:
        params = {}

    unit = int(params.get("unit", 1000))
    amount_per_unit = float(params.get("amount_per_unit", 100))
    if unit <= 0:
        return 0.0

    return (pay_jpy // unit) * amount_per_unit


def evaluate(
    promo: CardPromo,
    pay_jpy: int,
    pay_date: dt.date,
    merchant_type: str,
    usd_jpy: float,
) -> tuple[int, str]:
    if not promo.enabled:
        return 0, "카드 비활성화"

    if promo.start_date and pay_date < promo.start_date:
        return 0, "행사 시작 전"
    if promo.end_date and pay_date > promo.end_date:
        return 0, "행사 종료 후"

    if not merchant_match(promo.merchant_type, merchant_type):
        return 0, "가맹점 조건 불일치"

    if promo.used_count >= promo.max_uses:
        return 0, "사용 횟수 소진"

    pay_in_min_currency = convert(float(pay_jpy), "JPY", promo.min_currency, usd_jpy)
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
                usd_jpy,
            )
            raw = min(raw, per_txn_cap_jpy)
        reward_jpy = raw
        reason = f"정률 할인 {promo.percent_value:g}%"

    elif promo.reward_type == "fixed_cashback":
        reward_jpy = promo.fixed_amount
        reason = f"정액 캐시백 ¥{promo.fixed_amount:,.0f}"

    elif promo.reward_type == "cashback_with_cap":
        raw = pay_jpy * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            per_txn_cap_jpy = convert(
                promo.max_reward_per_txn,
                promo.max_reward_per_txn_currency,
                "JPY",
                usd_jpy,
            )
            raw = min(raw, per_txn_cap_jpy)
        reward_jpy = raw
        reason = f"캐시백 {promo.percent_value:g}% + 건당 한도"

    elif promo.reward_type == "formula_cashback":
        reward_jpy = calc_formula_cashback(pay_jpy, promo.formula_id, promo.formula_params_json)
        reason = f"공식 캐시백 ({promo.formula_id})"

    else:
        return 0, "지원하지 않는 리워드 타입"

    if promo.total_cap_amount > 0:
        total_cap_jpy = convert(promo.total_cap_amount, promo.total_cap_currency, "JPY", usd_jpy)
        total_used_jpy = convert(promo.total_used_amount, promo.total_cap_currency, "JPY", usd_jpy)
        remaining = max(total_cap_jpy - total_used_jpy, 0)
        if remaining <= 0:
            return 0, "총 한도 소진"
        reward_jpy = min(reward_jpy, remaining)
        reason += f" · 총한도 잔여 ¥{remaining:,.0f} 적용"

    return int(round(max(reward_jpy, 0))), reason


def seed_promotions() -> List[CardPromo]:
    return [
        CardPromo(
            card_name="KB 유니온페이",
            enabled=True,
            reward_type="percent_discount",
            start_date=dt.date(2026, 2, 14),
            end_date=dt.date(2026, 5, 13),
            min_amount=10000,
            min_currency="JPY",
            percent_value=15,
            fixed_amount=0,
            max_reward_per_txn=2000,
            max_reward_per_txn_currency="JPY",
            max_uses=5,
            used_count=0,
            total_cap_amount=0,
            total_cap_currency="JPY",
            total_used_amount=0,
            merchant_type="all",
            formula_id="",
            formula_params_json="",
        ),
        CardPromo(
            card_name="하나 트래블로그 유니온페이",
            enabled=True,
            reward_type="percent_discount",
            start_date=dt.date(2026, 2, 11),
            end_date=dt.date(2026, 4, 30),
            min_amount=50,
            min_currency="USD",
            percent_value=20,
            fixed_amount=0,
            max_reward_per_txn=10,
            max_reward_per_txn_currency="USD",
            max_uses=3,
            used_count=0,
            total_cap_amount=0,
            total_cap_currency="JPY",
            total_used_amount=0,
            merchant_type="all",
            formula_id="",
            formula_params_json="",
        ),
        CardPromo(
            card_name="KB Travelers",
            enabled=True,
            reward_type="fixed_cashback",
            start_date=dt.date(2026, 3, 1),
            end_date=dt.date(2026, 3, 31),
            min_amount=1000,
            min_currency="JPY",
            percent_value=0,
            fixed_amount=500,
            max_reward_per_txn=0,
            max_reward_per_txn_currency="JPY",
            max_uses=10,
            used_count=0,
            total_cap_amount=5000,
            total_cap_currency="JPY",
            total_used_amount=0,
            merchant_type="kb_cvs3",
            formula_id="",
            formula_params_json="",
        ),
    ]


def promo_rows(promos: List[CardPromo]) -> List[dict]:
    return [
        {
            "card_name": p.card_name,
            "enabled": p.enabled,
            "reward_type": p.reward_type,
            "start_date": p.start_date,
            "end_date": p.end_date,
            "min_amount": p.min_amount,
            "min_currency": p.min_currency,
            "percent_value": p.percent_value,
            "fixed_amount": p.fixed_amount,
            "max_reward_per_txn": p.max_reward_per_txn,
            "max_reward_per_txn_currency": p.max_reward_per_txn_currency,
            "max_uses": p.max_uses,
            "used_count": p.used_count,
            "total_cap_amount": p.total_cap_amount,
            "total_cap_currency": p.total_cap_currency,
            "total_used_amount": p.total_used_amount,
            "merchant_type": p.merchant_type,
            "formula_id": p.formula_id,
            "formula_params_json": p.formula_params_json,
        }
        for p in promos
    ]


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
                    min_amount=float(row.get("min_amount", 0)),
                    min_currency=str(row.get("min_currency", "JPY")),
                    percent_value=float(row.get("percent_value", 0)),
                    fixed_amount=float(row.get("fixed_amount", 0)),
                    max_reward_per_txn=float(row.get("max_reward_per_txn", 0)),
                    max_reward_per_txn_currency=str(
                        row.get("max_reward_per_txn_currency", "JPY")
                    ),
                    max_uses=int(row.get("max_uses", 0)),
                    used_count=int(row.get("used_count", 0)),
                    total_cap_amount=float(row.get("total_cap_amount", 0)),
                    total_cap_currency=str(row.get("total_cap_currency", "JPY")),
                    total_used_amount=float(row.get("total_used_amount", 0)),
                    merchant_type=str(row.get("merchant_type", "all")),
                    formula_id=str(row.get("formula_id", "")),
                    formula_params_json=str(row.get("formula_params_json", "")),
                )
            )
        except Exception:
            continue
    return promos


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
                "min_amount": float(r.get("min_amount", 0) or 0),
                "min_currency": r.get("min_currency", "JPY"),
                "percent_value": float(r.get("percent_value", 0) or 0),
                "fixed_amount": float(r.get("fixed_amount", 0) or 0),
                "max_reward_per_txn": float(r.get("max_reward_per_txn", 0) or 0),
                "max_reward_per_txn_currency": r.get("max_reward_per_txn_currency", "JPY"),
                "max_uses": int(r.get("max_uses", 0) or 0),
                "used_count": int(r.get("used_count", 0) or 0),
                "total_cap_amount": float(r.get("total_cap_amount", 0) or 0),
                "total_cap_currency": r.get("total_cap_currency", "JPY"),
                "total_used_amount": float(r.get("total_used_amount", 0) or 0),
                "merchant_type": r.get("merchant_type", "all"),
                "formula_id": r.get("formula_id", ""),
                "formula_params_json": r.get("formula_params_json", ""),
            }
        )

    return rows_to_promos(rows)


st.title("💳 일본 결제 카드 추천")
st.caption("JPY 결제 금액(정수)과 가맹점 버튼을 선택하면, 카드 행사 조건을 비교해 추천합니다.")

if "promos" not in st.session_state:
    st.session_state.promos = seed_promotions()

with st.container(border=True):
    left, right = st.columns([2, 1])
    with left:
        st.subheader("실시간 환율")
    with right:
        refresh = st.button("환율 새로고침", use_container_width=True)

    if refresh or "usd_jpy" not in st.session_state:
        st.session_state.usd_jpy = get_usd_jpy_rate()

    usd_jpy = st.session_state.usd_jpy
    if usd_jpy:
        st.success(f"1 USD = {usd_jpy:,.2f} JPY")
    else:
        st.error("환율 조회 실패. 잠시 후 다시 시도해 주세요.")

if "selected_merchant_type" not in st.session_state:
    st.session_state.selected_merchant_type = MERCHANT_NORMAL

with st.container(border=True):
    st.subheader("결제 정보")
    st.write("가맹점 유형")
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button(
            "일반",
            use_container_width=True,
            type="primary" if st.session_state.selected_merchant_type == MERCHANT_NORMAL else "secondary",
        ):
            st.session_state.selected_merchant_type = MERCHANT_NORMAL
    with col_b:
        if st.button(
            "KB 3대 편의점(세븐, 로손, 패밀리)",
            use_container_width=True,
            type="primary" if st.session_state.selected_merchant_type == MERCHANT_KB_CVS3 else "secondary",
        ):
            st.session_state.selected_merchant_type = MERCHANT_KB_CVS3

    merchant_type = st.session_state.selected_merchant_type
    pay_jpy = st.number_input(
        "결제 금액 (JPY)", min_value=0, step=1000, value=12000, format="%d"
    )
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

    rows = promo_rows(st.session_state.promos)
    edited = st.data_editor(
        rows,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "reward_type": st.column_config.SelectboxColumn(
                options=[
                    "percent_discount",
                    "fixed_cashback",
                    "cashback_with_cap",
                    "formula_cashback",
                ]
            ),
            "min_currency": st.column_config.SelectboxColumn(options=["JPY", "USD"]),
            "max_reward_per_txn_currency": st.column_config.SelectboxColumn(
                options=["JPY", "USD"]
            ),
            "total_cap_currency": st.column_config.SelectboxColumn(options=["JPY", "USD"]),
            "merchant_type": st.column_config.SelectboxColumn(options=["all", "kb_cvs3"]),
            "formula_params_json": st.column_config.TextColumn(
                help='예: {"unit":1000,"amount_per_unit":100}'
            ),
        },
    )
    st.session_state.promos = rows_to_promos(edited)

calc = st.button("최적 카드 계산", type="primary", use_container_width=True)

if calc:
    if not usd_jpy:
        st.warning("환율을 불러온 뒤 다시 계산해 주세요.")
    elif pay_jpy <= 0:
        st.warning("결제 금액을 0보다 크게 입력해 주세요.")
    else:
        results = []
        for promo in st.session_state.promos:
            reward_jpy, reason = evaluate(
                promo=promo,
                pay_jpy=int(pay_jpy),
                pay_date=pay_date,
                merchant_type=merchant_type,
                usd_jpy=usd_jpy,
            )
            reward_usd = convert(float(reward_jpy), "JPY", "USD", usd_jpy)
            results.append(
                {
                    "card_name": promo.card_name,
                    "reward_jpy": reward_jpy,
                    "reward_usd": reward_usd,
                    "reason": reason,
                }
            )

        results.sort(key=lambda x: x["reward_jpy"], reverse=True)

        if not results:
            st.info("등록된 카드 행사가 없습니다.")
        else:
            best = results[0]
            st.success(
                f"추천 카드: {best['card_name']} | 예상 혜택 ¥{best['reward_jpy']:,.0f} "
                f"(약 ${best['reward_usd']:,.2f})"
            )
            for i, row in enumerate(results, start=1):
                with st.container(border=True):
                    st.markdown(f"**{i}위. {row['card_name']}**")
                    st.write(
                        f"예상 혜택: ¥{row['reward_jpy']:,.0f} (약 ${row['reward_usd']:,.2f})"
                    )
                    st.caption(f"근거: {row['reason']}")

st.caption("배포용 참고: Streamlit Community Cloud에서 main 파일을 app.py로 지정하세요.")
