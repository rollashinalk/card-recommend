import csv
import datetime as dt
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

import requests
import streamlit as st

st.set_page_config(page_title="일본 카드 추천", page_icon="💳", layout="centered")

APP_STATE_PATH = Path("app_state.json")
MERCHANT_NORMAL = "일반"
MERCHANT_KB_CVS3 = "KB 3대 편의점(세븐, 로손, 패밀리)"
SUPPORTED_CURRENCIES = ["JPY", "USD", "KRW"]
KST = ZoneInfo("Asia/Seoul")


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
    monthly_spend_cap_amount: float
    monthly_spend_cap_currency: str
    monthly_spend_used_amount: float
    monthly_reward_cap_amount: float
    monthly_reward_cap_currency: str
    monthly_reward_used_amount: float
    merchant_type: str
    formula_id: str
    formula_params_json: str


@dataclass
class Txn:
    txn_id: str
    txn_date: dt.date
    card_name: str
    amount_jpy: int
    merchant_type: str
    status: str
    memo: str


@dataclass
class PromoState:
    used_count: int
    total_used_jpy: float
    month_spend_used: dict[str, float]
    month_reward_used: dict[str, float]


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if not value:
        return None
    return dt.date.fromisoformat(value)


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def month_key(d: dt.date) -> str:
    return d.strftime("%Y-%m")


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
    return promo_merchant_type == "kb_cvs3" and selected_merchant_type == MERCHANT_KB_CVS3


def calc_formula_cashback(pay_jpy: int, formula_id: str, fx_rates: dict[str, float]) -> tuple[float, str]:
    if formula_id != "shinhan_the_more_v1":
        return 0.0, "미지원 공식"
    pay_krw = int(round(convert(float(pay_jpy), "JPY", "KRW", fx_rates)))
    lower_thousand = pay_krw % 1000
    cashback_krw = lower_thousand * 2
    cashback_jpy = convert(float(cashback_krw), "KRW", "JPY", fx_rates)
    return cashback_jpy, f"더모아 공식: ({pay_krw:,}원 % 1000={lower_thousand:,}) x 2 = {cashback_krw:,}원"


def eval_for_payment(
    promo: CardPromo,
    pay_jpy: int,
    pay_date: dt.date,
    merchant_type: str,
    fx_rates: dict[str, float],
    state: PromoState,
) -> tuple[int, str, int, float, float, float]:
    if not promo.enabled:
        return 0, "카드 비활성화", 0, 0, 0, 0
    if promo.start_date and pay_date < promo.start_date:
        return 0, "행사 시작 전", 0, 0, 0, 0
    if promo.end_date and pay_date > promo.end_date:
        return 0, "행사 종료 후", 0, 0, 0, 0
    if not merchant_match(promo.merchant_type, merchant_type):
        return 0, "가맹점 조건 불일치", 0, 0, 0, 0
    if promo.max_uses > 0 and state.used_count >= promo.max_uses:
        return 0, "사용 횟수 소진", 0, 0, 0, 0

    pay_in_min_currency = convert(float(pay_jpy), "JPY", promo.min_currency, fx_rates)
    if pay_in_min_currency < promo.min_amount:
        return 0, f"최소 결제 금액 미달 ({promo.min_amount:g} {promo.min_currency})", 0, 0, 0, 0

    reward_jpy = 0.0
    reason = ""
    spend_inc = 0.0
    reward_inc = 0.0
    mkey = month_key(pay_date)

    if promo.reward_type == "percent_discount":
        reward_jpy = pay_jpy * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            reward_jpy = min(
                reward_jpy,
                convert(promo.max_reward_per_txn, promo.max_reward_per_txn_currency, "JPY", fx_rates),
            )
        reason = f"정률 할인 {promo.percent_value:g}%"

    elif promo.reward_type == "fixed_cashback":
        reward_jpy = convert(promo.fixed_amount, promo.min_currency, "JPY", fx_rates)
        reason = f"정액 혜택 {promo.fixed_amount:g} {promo.min_currency}"

    elif promo.reward_type == "cashback_with_cap":
        eligible_ratio = 1.0
        if promo.monthly_spend_cap_amount > 0:
            used = state.month_spend_used.get(mkey, 0.0)
            remain = max(promo.monthly_spend_cap_amount - used, 0)
            if remain <= 0:
                return 0, "월 결제금액 한도 소진", 0, 0, 0, 0
            pay_in_spend = convert(float(pay_jpy), "JPY", promo.monthly_spend_cap_currency, fx_rates)
            eligible_spend = min(pay_in_spend, remain)
            eligible_ratio = eligible_spend / pay_in_spend if pay_in_spend > 0 else 0.0
            spend_inc = eligible_spend

        reward_jpy = pay_jpy * eligible_ratio * (promo.percent_value / 100.0)
        if promo.max_reward_per_txn > 0:
            reward_jpy = min(
                reward_jpy,
                convert(promo.max_reward_per_txn, promo.max_reward_per_txn_currency, "JPY", fx_rates),
            )

        if promo.monthly_reward_cap_amount > 0:
            used_r = state.month_reward_used.get(mkey, 0.0)
            remain_r = max(promo.monthly_reward_cap_amount - used_r, 0)
            if remain_r <= 0:
                return 0, "월 캐시백 한도 소진", 0, 0, 0, 0
            remain_r_jpy = convert(remain_r, promo.monthly_reward_cap_currency, "JPY", fx_rates)
            reward_jpy = min(reward_jpy, remain_r_jpy)

        reward_inc = convert(reward_jpy, "JPY", promo.monthly_reward_cap_currency, fx_rates)
        reason = f"캐시백 {promo.percent_value:g}% + 월 한도 적용"

    elif promo.reward_type == "formula_cashback":
        reward_jpy, formula_reason = calc_formula_cashback(pay_jpy, promo.formula_id, fx_rates)
        reason = f"공식 혜택 ({promo.formula_id}) · {formula_reason}"

    else:
        return 0, "지원하지 않는 리워드 타입", 0, 0, 0, 0

    if promo.total_cap_amount > 0:
        total_cap_jpy = convert(promo.total_cap_amount, promo.total_cap_currency, "JPY", fx_rates)
        remain_total_jpy = max(total_cap_jpy - state.total_used_jpy, 0)
        if remain_total_jpy <= 0:
            return 0, "총 한도 소진", 0, 0, 0, 0
        reward_jpy = min(reward_jpy, remain_total_jpy)

    reward_jpy = int(round(max(reward_jpy, 0)))
    if reward_jpy <= 0:
        return 0, reason or "혜택 없음", 0, 0, 0, 0

    if promo.reward_type == "cashback_with_cap" and promo.monthly_reward_cap_amount > 0:
        reward_inc = convert(float(reward_jpy), "JPY", promo.monthly_reward_cap_currency, fx_rates)

    return reward_jpy, reason, 1, float(reward_jpy), spend_inc, reward_inc


def build_state_from_ledger(promo: CardPromo, txns: List[Txn], fx_rates: dict[str, float]) -> PromoState:
    stt = PromoState(
        used_count=promo.used_count,
        total_used_jpy=convert(promo.total_used_amount, promo.total_cap_currency, "JPY", fx_rates)
        if promo.total_used_amount > 0
        else 0.0,
        month_spend_used={},
        month_reward_used={},
    )
    approved = sorted(
        [t for t in txns if t.status == "approved" and t.card_name == promo.card_name],
        key=lambda x: (x.txn_date, x.txn_id),
    )
    for t in approved:
        reward, _, used_inc, total_inc_jpy, spend_inc, reward_inc = eval_for_payment(
            promo, t.amount_jpy, t.txn_date, t.merchant_type, fx_rates, stt
        )
        if reward > 0:
            stt.used_count += used_inc
            stt.total_used_jpy += total_inc_jpy
            key = month_key(t.txn_date)
            if promo.monthly_spend_cap_amount > 0 and spend_inc > 0:
                stt.month_spend_used[key] = stt.month_spend_used.get(key, 0.0) + spend_inc
            if promo.monthly_reward_cap_amount > 0 and reward_inc > 0:
                stt.month_reward_used[key] = stt.month_reward_used.get(key, 0.0) + reward_inc
    return stt


def seed_promotions() -> List[CardPromo]:
    return [
        CardPromo("KB UPI (가온 체크)", True, "percent_discount", dt.date(2026, 2, 14), dt.date(2026, 5, 13), 10000, "JPY", 15, 0, 2000, "JPY", 5, 0, 0, "JPY", 0, 0, "KRW", 0, 0, "KRW", 0, "all", "", ""),
        CardPromo("하나 UPI (트래블로그)", True, "percent_discount", dt.date(2026, 2, 11), dt.date(2026, 4, 30), 50, "USD", 20, 0, 10, "USD", 3, 0, 0, "JPY", 0, 0, "KRW", 0, 0, "KRW", 0, "all", "", ""),
        CardPromo("우리 UPI (SKT우리)", True, "percent_discount", dt.date(2025, 12, 22), dt.date(2026, 5, 31), 50, "USD", 11, 0, 15, "USD", 3, 0, 0, "JPY", 0, 0, "KRW", 0, 0, "KRW", 0, "all", "", ""),
        CardPromo("BC GOAT", True, "cashback_with_cap", None, None, 0, "USD", 6, 0, 0, "USD", 0, 0, 0, "KRW", 0, 1000000, "KRW", 0, 30000, "KRW", 0, "all", "", ""),
        CardPromo("KB 일본 편의점 행사 (KB 트래블러스)", True, "fixed_cashback", dt.date(2026, 3, 1), dt.date(2026, 3, 31), 1000, "JPY", 0, 500, 0, "JPY", 10, 0, 5000, "JPY", 0, 0, "KRW", 0, 0, "KRW", 0, "kb_cvs3", "", ""),
        CardPromo("신한 더모아", True, "formula_cashback", None, None, 0, "KRW", 0, 0, 0, "KRW", 0, 0, 0, "KRW", 0, 0, "KRW", 0, 0, "KRW", 0, "all", "shinhan_the_more_v1", ""),
    ]


def seed_transactions() -> List[Txn]:
    return []


def promo_rows(promos: List[CardPromo]) -> List[dict]:
    return [asdict(p) for p in promos]


def txn_rows(txns: List[Txn]) -> List[dict]:
    return [asdict(t) for t in txns]


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
                    max_reward_per_txn_currency=str(row.get("max_reward_per_txn_currency", "JPY")),
                    max_uses=int(row.get("max_uses", 0)),
                    used_count=int(row.get("used_count", 0)),
                    total_cap_amount=float(row.get("total_cap_amount", 0)),
                    total_cap_currency=str(row.get("total_cap_currency", "JPY")),
                    total_used_amount=float(row.get("total_used_amount", 0)),
                    monthly_spend_cap_amount=float(row.get("monthly_spend_cap_amount", 0)),
                    monthly_spend_cap_currency=str(row.get("monthly_spend_cap_currency", "KRW")),
                    monthly_spend_used_amount=float(row.get("monthly_spend_used_amount", 0)),
                    monthly_reward_cap_amount=float(row.get("monthly_reward_cap_amount", 0)),
                    monthly_reward_cap_currency=str(row.get("monthly_reward_cap_currency", "KRW")),
                    monthly_reward_used_amount=float(row.get("monthly_reward_used_amount", 0)),
                    merchant_type=str(row.get("merchant_type", "all")),
                    formula_id=str(row.get("formula_id", "")),
                    formula_params_json=str(row.get("formula_params_json", "")),
                )
            )
        except Exception:
            continue
    return promos


def rows_to_txns(rows: List[dict]) -> List[Txn]:
    out = []
    for r in rows:
        try:
            d = r.get("txn_date")
            if isinstance(d, str):
                d = dt.date.fromisoformat(d)
            out.append(
                Txn(
                    txn_id=str(r.get("txn_id", "")).strip() or f"txn-{len(out)+1}",
                    txn_date=d,
                    card_name=str(r.get("card_name", "")),
                    amount_jpy=int(float(r.get("amount_jpy", 0))),
                    merchant_type=str(r.get("merchant_type", MERCHANT_NORMAL)),
                    status=str(r.get("status", "approved")),
                    memo=str(r.get("memo", "")),
                )
            )
        except Exception:
            continue
    return out


def save_app_state(promos: List[CardPromo], txns: List[Txn]) -> None:
    payload = {"promos": [], "transactions": []}
    for p in promos:
        d = asdict(p)
        d["start_date"] = p.start_date.isoformat() if p.start_date else ""
        d["end_date"] = p.end_date.isoformat() if p.end_date else ""
        payload["promos"].append(d)
    for t in txns:
        d = asdict(t)
        d["txn_date"] = t.txn_date.isoformat()
        payload["transactions"].append(d)
    APP_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_app_state() -> tuple[List[CardPromo], List[Txn]]:
    if not APP_STATE_PATH.exists():
        return seed_promotions(), seed_transactions()
    try:
        data = json.loads(APP_STATE_PATH.read_text(encoding="utf-8"))
        promo_rows_in = data.get("promos", [])
        txn_rows_in = data.get("transactions", [])
        for r in promo_rows_in:
            r["start_date"] = parse_date(r.get("start_date", ""))
            r["end_date"] = parse_date(r.get("end_date", ""))
        for r in txn_rows_in:
            r["txn_date"] = parse_date(r.get("txn_date", ""))
        promos = rows_to_promos(promo_rows_in) or seed_promotions()
        txns = rows_to_txns(txn_rows_in)
        return promos, txns
    except Exception:
        return seed_promotions(), seed_transactions()


def inject_ui_theme() -> None:
    st.markdown(
        """
        <style>
        @import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css');

        :root {
            --off-white: #F8FAFC;
            --nova-blue: #0052FF;
            --electric-blue: #0052FF;
            --deep-navy: #0F172A;
            --muted: #475569;
            --card-shadow: 0 24px 48px rgba(15, 23, 42, 0.08);
            --card-shadow-hover: 0 30px 60px rgba(15, 23, 42, 0.12);
            --radius-card: 28px;
            --radius-pill: 999px;
        }

        * {
            font-family: 'Pretendard', sans-serif !important;
        }

        html, body, [data-testid="stAppViewContainer"], .stApp {
            background: var(--off-white) !important;
            color: var(--deep-navy) !important;
        }

        .main .block-container {
            max-width: 900px;
            padding: clamp(1rem, 4vw, 2.8rem);
        }

        .hero-banner {
            background: #ffffff;
            border-radius: var(--radius-card);
            padding: clamp(1.1rem, 2.8vw, 2rem);
            margin-bottom: clamp(1.1rem, 2.6vw, 2rem);
            box-shadow: var(--card-shadow);
            text-align: center;
        }

        .hero-banner .eyebrow {
            margin: 0;
            font-size: clamp(0.75rem, 1.4vw, 0.92rem);
            font-weight: 700;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--nova-blue);
        }

        .hero-banner h1 {
            font-size: clamp(1.7rem, 6vw, 3.1rem) !important;
            font-weight: 850 !important;
            letter-spacing: -0.05em !important;
            line-height: 1.03 !important;
            color: var(--deep-navy) !important;
            margin: clamp(0.45rem, 1.6vw, 0.75rem) 0 0 !important;
        }

        [data-testid="stHeadingWithActionElements"] h2,
        [data-testid="stHeadingWithActionElements"] h3 {
            color: var(--deep-navy) !important;
            letter-spacing: -0.03em;
            font-size: clamp(1.25rem, 3.2vw, 1.8rem) !important;
        }

        .rank-card {
            background: #ffffff;
            border: 1px solid #E2E8F0 !important;
            border-radius: var(--radius-card);
            padding: clamp(1.05rem, 2.8vw, 1.45rem);
            margin: 0.78rem 0;
            box-shadow: var(--card-shadow);
            transition: transform 0.22s ease, box-shadow 0.22s ease;
        }

        .rank-card:hover {
            transform: translateY(-3px);
            box-shadow: var(--card-shadow-hover);
        }

        .rank-card-1 { background: rgba(0, 82, 255, 0.05) !important; border: 1.5px solid var(--nova-blue) !important; }
        .rank-card-2 { background: rgba(124, 58, 237, 0.05) !important; border: 1px solid #DDD6FE !important; }
        .rank-card-3 { background: rgba(16, 185, 129, 0.05) !important; border: 1px solid #D1FAE5 !important; }
        .rank-card-plain { background: #ffffff !important; border: 1px solid #E2E8F0 !important; }

        .rank-title {
            font-size: clamp(1rem, 2.4vw, 1.2rem);
            font-weight: 760;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            color: var(--deep-navy);
        }

        .rank-benefit {
            font-size: clamp(1.06rem, 3vw, 1.35rem);
            font-weight: 830;
            color: var(--nova-blue);
            margin: 0.55rem 0 0.75rem;
            line-height: 1.3;
        }

        .compact-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 0.46rem;
        }

        .meta-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: var(--radius-pill);
            background: rgba(0, 82, 255, 0.08);
            color: #1e3a8a;
            font-size: clamp(0.78rem, 2vw, 0.9rem);
            font-weight: 620;
            line-height: 1;
            padding: 0.5rem 0.78rem;
            white-space: nowrap;
        }

        .stButton > button {
            min-height: 54px !important;
            border-radius: 14px !important;
            font-size: clamp(0.98rem, 2.2vw, 1.08rem) !important;
            font-weight: 700 !important;
            transition: transform 0.18s ease, background 0.18s ease, box-shadow 0.18s ease !important;
        }

        .stButton > button[kind="primary"],
        button[data-testid="baseButton-primary"],
        div[data-testid="stFormSubmitButton"] button {
            background: var(--deep-navy) !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: 0 12px 22px rgba(15, 23, 42, 0.2) !important;
            transition: all 0.2s ease !important;
        }

        .stButton > button[kind="primary"]:hover,
        button[data-testid="baseButton-primary"]:hover,
        div[data-testid="stFormSubmitButton"] button:hover {
            background: var(--electric-blue) !important;
            transform: scale(1.02) !important;
            box-shadow: 0 15px 30px rgba(0, 82, 255, 0.3) !important;
        }

        div[data-baseweb="input"] {
            border-radius: 14px !important;
        }

        button[role="tab"] {
            font-size: clamp(0.9rem, 2vw, 1rem) !important;
            height: 46px !important;
            color: var(--deep-navy) !important;
        }

        button[role="tab"][aria-selected="true"] {
            color: var(--nova-blue) !important;
            border-bottom: 2px solid var(--nova-blue) !important;
        }

        [data-baseweb="tab-highlight"] {
            background-color: var(--nova-blue) !important;
        }

        .rate-updated {
            color: var(--muted);
            font-size: 0.82rem;
            margin-top: 0.2rem;
        }

        .section-spacer {
            height: 1rem;
        }

        [data-testid="stHorizontalBlock"]:has(.st-key-fx_refresh_small) {
            background: white;
            border: 1px solid #E2E8F0;
            border-radius: 16px;
            padding: 0.75rem 1rem 1rem;
            box-shadow: var(--card-shadow);
            align-items: center;
            margin-top: 0.3rem;
            gap: 0.3rem;
            flex-direction: row !important;
            flex-wrap: nowrap !important;
        }

        [data-testid="stHorizontalBlock"]:has(.st-key-fx_refresh_small) > [data-testid="column"]:first-child {
            flex: 1 1 auto !important;
        }

        [data-testid="stHorizontalBlock"]:has(.st-key-fx_refresh_small) > [data-testid="column"]:has(.st-key-fx_refresh_small) {
            flex: 0 0 auto !important;
            width: auto !important;
            display: flex;
            justify-content: flex-end;
            align-items: center;
            padding-right: 0.75rem !important;
        }

        .st-key-fx_refresh_small button {
            min-height: 36px !important;
            width: 36px !important;
            padding: 0 !important;
            border: none !important;
            background: transparent !important;
            font-size: 1.2rem !important;
            box-shadow: none !important;
        }

        div[data-testid="stForm"],
        div[data-testid="stDataEditor"],
        div[data-testid="stFileUploader"] {
            background: #ffffff !important;
            border-radius: var(--radius-card);
            box-shadow: var(--card-shadow);
            border: 1px solid #E2E8F0;
            padding: clamp(0.8rem, 2.2vw, 1.2rem);
        }

        div[data-testid="stDataEditor"] {
            overflow: hidden;
        }

        div[data-baseweb="input"],
        div[data-baseweb="select"],
        div[data-baseweb="base-input"],
        [data-testid="stDateInput"] div[data-baseweb="input"] {
            border-radius: 14px !important;
        }

        @media screen and (max-width: 768px) {
            .main .block-container {
                padding: 1rem;
            }

            .hero-banner {
                border-radius: 22px;
            }

            .rank-card {
                border-radius: 22px;
            }

            .meta-badge {
                white-space: normal;
            }


        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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
                "min_currency": r.get("min_currency", "JPY") or "JPY",
                "percent_value": float(r.get("percent_value", 0) or 0),
                "fixed_amount": float(r.get("fixed_amount", 0) or 0),
                "max_reward_per_txn": float(r.get("max_reward_per_txn", 0) or 0),
                "max_reward_per_txn_currency": r.get("max_reward_per_txn_currency", "JPY") or "JPY",
                "max_uses": int(r.get("max_uses", 0) or 0),
                "used_count": int(r.get("used_count", 0) or 0),
                "total_cap_amount": float(r.get("total_cap_amount", 0) or 0),
                "total_cap_currency": r.get("total_cap_currency", "JPY") or "JPY",
                "total_used_amount": float(r.get("total_used_amount", 0) or 0),
                "monthly_spend_cap_amount": float(r.get("monthly_spend_cap_amount", 0) or 0),
                "monthly_spend_cap_currency": r.get("monthly_spend_cap_currency", "KRW") or "KRW",
                "monthly_spend_used_amount": float(r.get("monthly_spend_used_amount", 0) or 0),
                "monthly_reward_cap_amount": float(r.get("monthly_reward_cap_amount", 0) or 0),
                "monthly_reward_cap_currency": r.get("monthly_reward_cap_currency", "KRW") or "KRW",
                "monthly_reward_used_amount": float(r.get("monthly_reward_used_amount", 0) or 0),
                "merchant_type": r.get("merchant_type", "all") or "all",
                "formula_id": r.get("formula_id", "") or "",
                "formula_params_json": r.get("formula_params_json", "") or "",
            }
        )
    return rows_to_promos(rows)


inject_ui_theme()
st.markdown(
    """
    <div class="hero-banner">
      <p class="eyebrow">💳 Card Optimizer</p>
      <h1>일본 결제 카드 추천</h1>
    </div>
    """,
    unsafe_allow_html=True,
)

if "promos" not in st.session_state or "transactions" not in st.session_state:
    loaded_promos, loaded_txns = load_app_state()
    st.session_state.promos = loaded_promos
    st.session_state.transactions = loaded_txns
if "merchant_type_input" not in st.session_state:
    st.session_state.merchant_type_input = MERCHANT_NORMAL
if "pay_jpy_input" not in st.session_state:
    st.session_state.pay_jpy_input = 0
if "reset_pay_jpy" not in st.session_state:
    st.session_state.reset_pay_jpy = False
if "pending_calc" not in st.session_state:
    st.session_state.pending_calc = None
if "selected_option_idx" not in st.session_state:
    st.session_state.selected_option_idx = 0
if "fx_updated_at" not in st.session_state:
    st.session_state.fx_updated_at = None


def format_money(amount: float, currency: str) -> str:
    if currency in ["JPY", "KRW"]:
        return f"{currency} {amount:,.0f}"
    return f"{currency} {amount:,.2f}"


def benefit_display_currency(promo: CardPromo) -> str:
    if promo.reward_type == "formula_cashback":
        return "KRW"
    if promo.reward_type == "cashback_with_cap" and promo.monthly_reward_cap_amount > 0:
        return promo.monthly_reward_cap_currency
    if promo.reward_type == "fixed_cashback":
        return promo.min_currency
    if promo.max_reward_per_txn > 0:
        return promo.max_reward_per_txn_currency
    return "JPY"


tab_reco, tab_promo = st.tabs(["💳 카드 추천", "🏪 행사 관리"])

with tab_reco:
    with st.container():
        if "fx_rates" not in st.session_state:
            st.session_state.fx_rates = get_fx_rates()
            st.session_state.fx_updated_at = dt.datetime.now(tz=KST) if st.session_state.fx_rates else None

        fx_rates = st.session_state.fx_rates

        st.markdown("<p style='font-weight: 800; font-size: 0.95rem; color: #475569; margin: 0 0 0 5px;'>💱 실시간 환율</p>", unsafe_allow_html=True)
        fx_col1, fx_col2 = st.columns([0.85, 0.15], gap="small")

        with fx_col1:
            if fx_rates:
                jpy_to_krw = (fx_rates['KRW'] / fx_rates['JPY']) * 100
                updated_text = st.session_state.fx_updated_at.strftime("%H:%M") if st.session_state.fx_updated_at else "-"
                st.markdown(
                    f"""
                    <div style='color: var(--nova-blue); font-weight: 750; font-size: 0.95rem; line-height: 1.4; margin-top: 5px;'>
                        <div>1 USD = {fx_rates['JPY']:,.2f} JPY</div>
                        <div>100 JPY = {jpy_to_krw:,.2f} KRW</div>
                    </div>
                    <p style='color: #94a3b8; font-size: 0.75rem; margin: 5px 0 5px 0;'>업데이트: {updated_text}</p>
                    """,
                    unsafe_allow_html=True,
                )

        with fx_col2:
            refresh = st.button("🔄", key="fx_refresh_small", use_container_width=False)

        if refresh:
            st.session_state.fx_rates = get_fx_rates()
            st.session_state.fx_updated_at = dt.datetime.now(tz=KST) if st.session_state.fx_rates else None
            fx_rates = st.session_state.fx_rates

        if not fx_rates:
            st.error("환율 조회 실패. 잠시 후 다시 시도해 주세요.")

    st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

    with st.container():
        st.subheader("💴 결제 정보")
        if st.session_state.reset_pay_jpy:
            st.session_state.pay_jpy_input = 0
            st.session_state.reset_pay_jpy = False
        merchant_type = st.radio(
            "가맹점 유형",
            [MERCHANT_NORMAL, MERCHANT_KB_CVS3],
            horizontal=True,
            key="merchant_type_input",
        )
        pay_jpy = st.number_input(
            "결제 금액 (JPY)", min_value=0, step=1000, key="pay_jpy_input", format="%d"
        )
        pay_date = st.date_input("결제 날짜", value=dt.date.today())
        calculate_clicked = st.button("최적 카드 계산", type="primary", use_container_width=True)

    st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

    if calculate_clicked:
        if not fx_rates:
            st.warning("환율을 불러온 뒤 다시 계산해 주세요.")
        elif pay_jpy <= 0:
            st.warning("결제 금액을 0보다 크게 입력해 주세요.")
        else:
            options = []
            for promo in st.session_state.promos:
                state = build_state_from_ledger(promo, st.session_state.transactions, fx_rates)
                reward_jpy, reason, _, _, _, _ = eval_for_payment(
                    promo, int(pay_jpy), pay_date, merchant_type, fx_rates, state
                )
                display_cur = benefit_display_currency(promo)
                reward_native = convert(float(reward_jpy), "JPY", display_cur, fx_rates) if reward_jpy > 0 else 0.0
                remaining_uses = "무제한" if promo.max_uses <= 0 else max(promo.max_uses - state.used_count, 0)
                total_remain_text = "제한없음"
                if promo.total_cap_amount > 0:
                    total_remain_jpy = max(
                        convert(promo.total_cap_amount, promo.total_cap_currency, "JPY", fx_rates) - state.total_used_jpy,
                        0,
                    )
                    total_remain_text = format_money(total_remain_jpy, "JPY")

                mkey = month_key(pay_date)
                monthly_remain_text = "제한없음"
                if promo.monthly_reward_cap_amount > 0:
                    used_r = state.month_reward_used.get(mkey, 0.0)
                    rem_r = max(promo.monthly_reward_cap_amount - used_r, 0)
                    monthly_remain_text = format_money(rem_r, promo.monthly_reward_cap_currency)

                options.append(
                    {
                        "card_name": promo.card_name,
                        "reward_jpy": reward_jpy,
                        "reward_native": reward_native,
                        "reward_currency": display_cur,
                        "reason": reason,
                        "remaining_uses": remaining_uses,
                        "total_remain_text": total_remain_text,
                        "monthly_remain_text": monthly_remain_text,
                    }
                )

            options.sort(key=lambda x: x["reward_jpy"], reverse=True)
            st.session_state.pending_calc = {
                "pay_jpy": int(pay_jpy),
                "pay_date": pay_date.isoformat(),
                "merchant_type": merchant_type,
                "options": options,
            }
            st.session_state.selected_option_idx = 0
            st.session_state.reset_pay_jpy = True
            st.rerun()

    if st.session_state.pending_calc:
        data = st.session_state.pending_calc
        st.subheader("🔍 결제 카드 선택")

        for i, opt in enumerate(data["options"], start=1):
            with st.container():
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else ""
                rank_cls = f"rank-card rank-card-{i}" if i <= 3 else "rank-card rank-card-plain"
                st.markdown(
                    (
                        f"<div class='{rank_cls.strip()}'>"
                        f"<div class='rank-title'><span>{medal}</span> {i}위 · {opt['card_name']}</div>"
                        f"<div class='rank-benefit'>{format_money(opt['reward_native'], opt['reward_currency'])} 혜택</div>"
                        f"<div class='compact-meta'>"
                        f"<span class='meta-badge'>🚩 {opt['remaining_uses']}회 남음</span>"
                        f"<span class='meta-badge'>🧮 비교 JPY {opt['reward_jpy']:,.0f}</span>"
                        f"<span class='meta-badge'>💰 총한도 {opt['total_remain_text']}</span>"
                        f"<span class='meta-badge'>📅 월한도 {opt['monthly_remain_text']}</span>"
                        f"</div>"
                        f"</div>"
                    ),
                    unsafe_allow_html=True,
                )

        labels = [
            f"{i+1}위 · {o['card_name']} · {format_money(o['reward_native'], o['reward_currency'])}"
            for i, o in enumerate(data["options"])
        ]
        st.radio(
            "✔️ 실제 결제할 카드 선택",
            options=list(range(len(labels))),
            format_func=lambda idx: labels[idx],
            key="selected_option_idx",
        )

        col_done, col_cancel = st.columns(2)
        with col_done:
            if st.button("결제 완료", type="primary", use_container_width=True):
                chosen = data["options"][st.session_state.selected_option_idx]
                txns = st.session_state.transactions
                new_id = f"txn-{len(txns)+1}-{int(dt.datetime.now().timestamp())}"
                txns.append(
                    Txn(
                        txn_id=new_id,
                        txn_date=dt.date.fromisoformat(data["pay_date"]),
                        card_name=chosen["card_name"],
                        amount_jpy=int(data["pay_jpy"]),
                        merchant_type=data["merchant_type"],
                        status="approved",
                        memo="결제 완료 버튼으로 추가",
                    )
                )
                st.session_state.transactions = txns
                st.session_state.pending_calc = None
                save_app_state(st.session_state.promos, st.session_state.transactions)
                st.success("결제 내역이 원장에 추가되었습니다.")
                st.rerun()
        with col_cancel:
            if st.button("취소", use_container_width=True):
                st.session_state.pending_calc = None
                st.info("결제 추가 없이 취소되었습니다.")
                st.rerun()

    st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

    with st.container():
        st.subheader("📒 결제내역")
        card_names = sorted({p.card_name for p in st.session_state.promos})
        edited_txns = st.data_editor(
            txn_rows(st.session_state.transactions),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "txn_date": st.column_config.DateColumn(),
                "card_name": st.column_config.SelectboxColumn(options=card_names),
                "amount_jpy": st.column_config.NumberColumn(min_value=0, step=1000),
                "merchant_type": st.column_config.SelectboxColumn(options=[MERCHANT_NORMAL, MERCHANT_KB_CVS3]),
                "status": st.column_config.SelectboxColumn(options=["approved", "cancelled"]),
            },
        )
        st.session_state.transactions = rows_to_txns(edited_txns)
        save_app_state(st.session_state.promos, st.session_state.transactions)

        if st.button("초기화", type="secondary"):
            st.session_state.transactions = []
            save_app_state(st.session_state.promos, st.session_state.transactions)
            st.rerun()

with tab_promo:
    with st.container():
        st.subheader("🏪 행사 관리")

        st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

        with st.form("promo_add_form", clear_on_submit=True):
            r1c1, r1c2 = st.columns(2, gap="large")
            with r1c1:
                card_name = st.text_input("대상 카드", placeholder="예: KB UPI (가온 체크)")
            with r1c2:
                percent_value = st.number_input("할인율(%)", min_value=0.0, max_value=100.0, step=0.1, value=0.0)

            r2c1, r2c2 = st.columns(2, gap="large")
            with r2c1:
                reward_type = st.selectbox(
                    "혜택 유형",
                    ["percent_discount", "fixed_cashback", "cashback_with_cap", "formula_cashback"],
                )
            with r2c2:
                fixed_amount = st.number_input("정액 혜택", min_value=0.0, step=100.0, value=0.0)

            r3c1, r3c2 = st.columns(2, gap="large")
            with r3c1:
                start_date = st.date_input("시작일", value=dt.date.today(), key="promo_start_date")
            with r3c2:
                max_reward_per_txn = st.number_input("건당 최대 혜택", min_value=0.0, step=100.0, value=0.0)

            r4c1, r4c2 = st.columns(2, gap="large")
            with r4c1:
                end_date = st.date_input("종료일", value=dt.date.today(), key="promo_end_date")
            with r4c2:
                max_reward_cur = st.selectbox("건당 최대 혜택 통화", SUPPORTED_CURRENCIES, index=0)

            r5c1, r5c2 = st.columns(2, gap="large")
            with r5c1:
                min_amount = st.number_input("최소 결제 금액", min_value=0.0, step=1000.0, value=0.0)
            with r5c2:
                max_uses = st.number_input("최대 사용 횟수", min_value=0, step=1, value=0)

            r6c1, r6c2 = st.columns(2, gap="large")
            with r6c1:
                min_currency = st.selectbox("최소 금액 통화", SUPPORTED_CURRENCIES, index=0)
            with r6c2:
                merchant_type = st.selectbox("가맹점 유형", ["all", "kb_cvs3"])

            submitted = st.form_submit_button("행사 추가", type="primary", use_container_width=True)

            if submitted:
                if not card_name.strip():
                    st.warning("대상 카드명을 입력해 주세요.")
                else:
                    new_promo = CardPromo(
                        card_name=card_name.strip(),
                        enabled=True,
                        reward_type=reward_type,
                        start_date=start_date,
                        end_date=end_date,
                        min_amount=float(min_amount),
                        min_currency=min_currency,
                        percent_value=float(percent_value),
                        fixed_amount=float(fixed_amount),
                        max_reward_per_txn=float(max_reward_per_txn),
                        max_reward_per_txn_currency=max_reward_cur,
                        max_uses=int(max_uses),
                        used_count=0,
                        total_cap_amount=0.0,
                        total_cap_currency="JPY",
                        total_used_amount=0.0,
                        monthly_spend_cap_amount=0.0,
                        monthly_spend_cap_currency="KRW",
                        monthly_spend_used_amount=0.0,
                        monthly_reward_cap_amount=0.0,
                        monthly_reward_cap_currency="KRW",
                        monthly_reward_used_amount=0.0,
                        merchant_type=merchant_type,
                        formula_id="shinhan_the_more_v1" if reward_type == "formula_cashback" else "",
                        formula_params_json="",
                    )
                    st.session_state.promos.append(new_promo)
                    save_app_state(st.session_state.promos, st.session_state.transactions)
                    st.success("행사가 추가되었습니다.")
                    st.rerun()

    st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

    with st.container():
        st.subheader("📒 행사 리스트")
        uploaded_csv = st.file_uploader("프로모션 CSV 업로드", type=["csv"])
        if uploaded_csv is not None:
            try:
                st.session_state.promos = load_promos_from_csv(uploaded_csv)
                save_app_state(st.session_state.promos, st.session_state.transactions)
                st.success("CSV를 불러왔습니다.")
            except Exception as exc:
                st.error(f"CSV 파싱 실패: {exc}")

        st.markdown("<div class='section-spacer'></div>", unsafe_allow_html=True)

        edited_promos = st.data_editor(
            promo_rows(st.session_state.promos),
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "reward_type": st.column_config.SelectboxColumn(options=["percent_discount", "fixed_cashback", "cashback_with_cap", "formula_cashback"]),
                "min_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
                "max_reward_per_txn_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
                "total_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
                "monthly_spend_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
                "monthly_reward_cap_currency": st.column_config.SelectboxColumn(options=SUPPORTED_CURRENCIES),
                "merchant_type": st.column_config.SelectboxColumn(options=["all", "kb_cvs3"]),
            },
        )
        st.session_state.promos = rows_to_promos(edited_promos)
        save_app_state(st.session_state.promos, st.session_state.transactions)

