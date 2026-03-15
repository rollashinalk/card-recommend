import datetime as dt
from dataclasses import dataclass
from typing import List

import requests
import streamlit as st

st.set_page_config(page_title="일본 카드 추천", page_icon="💳", layout="centered")


@dataclass
class CardPromo:
    card_name: str
    enabled: bool
    start_date: dt.date
    end_date: dt.date
    min_amount: float
    min_currency: str
    discount_percent: float
    max_discount: float
    max_currency: str
    max_uses: int
    used_count: int


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


def evaluate(promo: CardPromo, pay_jpy: int, pay_date: dt.date, usd_jpy: float) -> tuple[int, str]:
    if not promo.enabled:
        return 0, "카드 비활성화"
    if not (promo.start_date <= pay_date <= promo.end_date):
        return 0, "행사 기간 아님"
    if promo.used_count >= promo.max_uses:
        return 0, "사용 횟수 소진"

    pay_in_min_currency = convert(float(pay_jpy), "JPY", promo.min_currency, usd_jpy)
    if pay_in_min_currency < promo.min_amount:
        return 0, f"최소 결제 금액 미달 ({promo.min_amount:g} {promo.min_currency})"

    raw_discount_jpy = pay_jpy * (promo.discount_percent / 100.0)
    max_discount_jpy = convert(promo.max_discount, promo.max_currency, "JPY", usd_jpy)
    discount_jpy = int(round(min(raw_discount_jpy, max_discount_jpy)))
    reason = (
        f"할인율 {promo.discount_percent:g}% 적용, "
        f"건당 최대 {promo.max_discount:g} {promo.max_currency}"
    )
    return discount_jpy, reason


def seed_promotions() -> List[CardPromo]:
    return [
        CardPromo(
            card_name="KB 유니온페이",
            enabled=True,
            start_date=dt.date(2026, 2, 14),
            end_date=dt.date(2026, 5, 13),
            min_amount=10000,
            min_currency="JPY",
            discount_percent=15,
            max_discount=2000,
            max_currency="JPY",
            max_uses=5,
            used_count=0,
        ),
        CardPromo(
            card_name="하나 트래블로그 유니온페이",
            enabled=True,
            start_date=dt.date(2026, 2, 11),
            end_date=dt.date(2026, 4, 30),
            min_amount=50,
            min_currency="USD",
            discount_percent=20,
            max_discount=10,
            max_currency="USD",
            max_uses=3,
            used_count=0,
        ),
    ]


st.title("💳 일본 결제 카드 추천")
st.caption("JPY 결제 금액(정수)을 입력하면, 카드 행사 조건을 비교해 가장 이득인 카드를 보여줍니다.")

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

with st.container(border=True):
    st.subheader("결제 정보")
    pay_jpy = st.number_input(
        "결제 금액 (JPY)", min_value=0, step=1000, value=12000, format="%d"
    )
    pay_date = st.date_input("결제 날짜", value=dt.date.today())

with st.container(border=True):
    st.subheader("카드/행사 목록")
    st.caption("필요한 행사 항목만 간단히 수정할 수 있습니다.")

    rows = []
    for p in st.session_state.promos:
        rows.append(
            {
                "card_name": p.card_name,
                "enabled": p.enabled,
                "start_date": p.start_date,
                "end_date": p.end_date,
                "min_amount": p.min_amount,
                "min_currency": p.min_currency,
                "discount_percent": p.discount_percent,
                "max_discount": p.max_discount,
                "max_currency": p.max_currency,
                "max_uses": p.max_uses,
                "used_count": p.used_count,
            }
        )

    edited = st.data_editor(
        rows,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "min_currency": st.column_config.SelectboxColumn(options=["JPY", "USD"]),
            "max_currency": st.column_config.SelectboxColumn(options=["JPY", "USD"]),
        },
    )

    updated_promos = []
    for row in edited:
        try:
            updated_promos.append(
                CardPromo(
                    card_name=str(row["card_name"]),
                    enabled=bool(row["enabled"]),
                    start_date=row["start_date"],
                    end_date=row["end_date"],
                    min_amount=float(row["min_amount"]),
                    min_currency=str(row["min_currency"]),
                    discount_percent=float(row["discount_percent"]),
                    max_discount=float(row["max_discount"]),
                    max_currency=str(row["max_currency"]),
                    max_uses=int(row["max_uses"]),
                    used_count=int(row["used_count"]),
                )
            )
        except Exception:
            continue

    st.session_state.promos = updated_promos

calc = st.button("최적 카드 계산", type="primary", use_container_width=True)

if calc:
    if not usd_jpy:
        st.warning("환율을 불러온 뒤 다시 계산해 주세요.")
    elif pay_jpy <= 0:
        st.warning("결제 금액을 0보다 크게 입력해 주세요.")
    else:
        results = []
        for promo in st.session_state.promos:
            discount_jpy, reason = evaluate(
                promo=promo,
                pay_jpy=int(pay_jpy),
                pay_date=pay_date,
                usd_jpy=usd_jpy,
            )
            discount_usd = convert(float(discount_jpy), "JPY", "USD", usd_jpy)
            results.append(
                {
                    "card_name": promo.card_name,
                    "discount_jpy": discount_jpy,
                    "discount_usd": discount_usd,
                    "reason": reason,
                }
            )

        results.sort(key=lambda x: x["discount_jpy"], reverse=True)

        if not results:
            st.info("등록된 카드 행사가 없습니다.")
        else:
            best = results[0]
            st.success(
                f"추천 카드: {best['card_name']} | 예상 할인 ¥{best['discount_jpy']:,.0f} "
                f"(약 ${best['discount_usd']:,.2f})"
            )
            for i, row in enumerate(results, start=1):
                with st.container(border=True):
                    st.markdown(f"**{i}위. {row['card_name']}**")
                    st.write(
                        f"예상 할인: ¥{row['discount_jpy']:,.0f} (약 ${row['discount_usd']:,.2f})"
                    )
                    st.caption(f"근거: {row['reason']}")

st.caption("배포용 참고: Streamlit Community Cloud에서 main 파일을 app.py로 지정하세요.")
