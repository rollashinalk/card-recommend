import csv
import datetime as dt
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

import requests
import streamlit as st

st.set_page_config(page_title="일본 카드 추천", page_icon="💳", layout="centered")

APP_STATE_PATH = Path("app_state.json")
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
        :root {
            --bg: #f5f8ff;
            --card: #ffffff;
            --line: #dbe5f5;
            --text: #111827;
            --muted: #667085;
            --primary: #3563ff;
            --primary-dark: #274dd6;
            --shadow: 0 16px 36px rgba(30, 64, 175, 0.10);
            --radius: 18px;
        }

        .stApp {
            background:
                radial-gradient(circle at 12% -20%, #dbeafe 0%, transparent 45%),
                radial-gradient(circle at 90% 8%, #e0e7ff 0%, transparent 30%),
                var(--bg);
            color: var(--text);
        }

        .main .block-container {
            max-width: 920px;
            padding-top: 1.6rem;
            padding-bottom: 3rem;
        }

        .hero-banner {
            border: 1px solid var(--line);
            border-radius: 22px;
            background: linear-gradient(135deg, #ffffff 0%, #f8faff 100%);
            box-shadow: var(--shadow);
            padding: 1.1rem 1.25rem;
            margin-bottom: 0.75rem;
        }

        .hero-banner .eyebrow {
            margin: 0;
            color: #3555cc;
            font-size: .76rem;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
        }

        .hero-banner h1 {
            margin: .2rem 0 .4rem;
            font-size: clamp(1.5rem, 2.3vw, 2.1rem);
            letter-spacing: -.02em;
            line-height: 1.2;
        }

        .hero-banner p {
            margin: 0;
            color: var(--muted);
            font-size: .95rem;
        }

        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: var(--radius);
            border: 1px solid var(--line);
            box-shadow: var(--shadow);
            background: rgba(255,255,255,0.90);
            backdrop-filter: blur(8px);
        }

        h2, h3 {
            letter-spacing: -.01em;
        }

        div[data-baseweb="input"] input,
        div[data-baseweb="select"] > div,
        div[data-baseweb="base-input"] input,
        textarea {
            border-radius: 12px !important;
            border-color: var(--line) !important;
            min-height: 42px;
        }

        div[data-baseweb="input"] input:focus,
        div[data-baseweb="base-input"] input:focus,
        textarea:focus {
            box-shadow: 0 0 0 3px rgba(53,99,255,.14);
        }

        .stButton > button {
            border-radius: 12px;
            min-height: 42px;
            font-weight: 650;
            border: 1px solid transparent;
        }

        .stButton > button[kind="primary"] {
            color: #fff;
            background: linear-gradient(180deg, #4d75ff 0%, #355ff3 100%);
            border-color: #355ff3;
        }

        .stButton > button:hover {
            filter: brightness(0.97);
        }

        div[data-testid="stRadio"] label p,
        div[data-testid="stMarkdownContainer"] p {
            color: var(--text);
        }

        @media (max-width: 768px) {
            .main .block-container { padding-top: 1rem; }
            .hero-banner { padding: 1rem; }
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
      <p class="eyebrow">Card Optimizer</p>
      <h1>💳 일본 결제 카드 추천</h1>
      <p>결제내역 원장 기반으로 혜택 누적/취소 반영을 자동 계산합니다.</p>
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
    st.subheader("결제 카드 선택")

    for i, opt in enumerate(data["options"], start=1):
        with st.container(border=True):
            st.markdown(f"**{i}위. {opt['card_name']}**")
            st.write(
                f"예상 혜택: {format_money(opt['reward_native'], opt['reward_currency'])} "
                f"(비교기준 JPY {opt['reward_jpy']:,.0f})"
            )
            st.caption(
                f"잔여 횟수: {opt['remaining_uses']} | 총 한도 잔여: {opt['total_remain_text']} | "
                f"월 혜택 잔여: {opt['monthly_remain_text']} | 근거: {opt['reason']}"
            )

    labels = [
        f"{i+1}위 · {o['card_name']} · {format_money(o['reward_native'], o['reward_currency'])}"
        for i, o in enumerate(data["options"])
    ]
    st.radio(
        "실제 결제할 카드 선택",
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

with st.container(border=True):
    st.subheader("결제내역 원장 (취소관리 포함)")
    st.caption("status가 cancelled인 건은 누적 혜택 계산에서 자동 제외됩니다.")
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

    if st.button("원장 초기화", type="secondary"):
        st.session_state.transactions = []
        save_app_state(st.session_state.promos, st.session_state.transactions)
        st.rerun()

with st.container(border=True):
    st.subheader("행사 리스트")
    uploaded_csv = st.file_uploader("프로모션 CSV 업로드", type=["csv"])
    if uploaded_csv is not None:
        try:
            st.session_state.promos = load_promos_from_csv(uploaded_csv)
            save_app_state(st.session_state.promos, st.session_state.transactions)
            st.success("CSV를 불러왔습니다.")
        except Exception as exc:
            st.error(f"CSV 파싱 실패: {exc}")

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

st.caption("배포용 참고: Streamlit Community Cloud에서 main 파일을 app.py로 지정하세요.")
