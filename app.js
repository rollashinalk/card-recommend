const STORAGE_KEY = "card-recommend-data-v1";

const defaultData = {
  cards: [
    { id: crypto.randomUUID(), name: "KB 유니온페이", enabled: true },
    { id: crypto.randomUUID(), name: "하나 트래블로그 유니온페이", enabled: true }
  ],
  promotions: []
};

function todayString() {
  return new Date().toISOString().slice(0, 10);
}

function loadState() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    return withDefaultPromotions(defaultData);
  }

  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed.cards) || !Array.isArray(parsed.promotions)) {
      return withDefaultPromotions(defaultData);
    }
    return parsed;
  } catch {
    return withDefaultPromotions(defaultData);
  }
}

function withDefaultPromotions(seed) {
  const data = JSON.parse(JSON.stringify(seed));
  const kb = data.cards.find((c) => c.name.includes("KB"));
  const hana = data.cards.find((c) => c.name.includes("하나"));

  if (kb) {
    data.promotions.push({
      id: crypto.randomUUID(),
      name: "KB 유니온페이 일본 오프라인 할인",
      cardId: kb.id,
      startDate: "2026-02-14",
      endDate: "2026-05-13",
      country: "JP",
      channel: "offline",
      minAmount: 10000,
      minCurrency: "JPY",
      discountPercent: 15,
      maxDiscount: 2000,
      maxCurrency: "JPY",
      maxUses: 5,
      usedCount: 0,
      totalLimit: 5000,
      totalLimitCurrency: "JPY",
      totalUsed: 0,
      monthlyLimit: 2000,
      monthlyLimitCurrency: "JPY",
      monthlyUsed: 0
    });
  }

  if (hana) {
    data.promotions.push({
      id: crypto.randomUUID(),
      name: "하나 트래블로그 유니온페이 일본/중국/베트남 할인",
      cardId: hana.id,
      startDate: "2026-02-11",
      endDate: "2026-04-30",
      country: "JP",
      channel: "offline",
      minAmount: 50,
      minCurrency: "USD",
      discountPercent: 20,
      maxDiscount: 10,
      maxCurrency: "USD",
      maxUses: 3,
      usedCount: 0,
      totalLimit: 30,
      totalLimitCurrency: "USD",
      totalUsed: 0,
      monthlyLimit: 20,
      monthlyLimitCurrency: "USD",
      monthlyUsed: 0
    });
  }

  return data;
}

function saveState() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

const state = loadState();
let usdToJpyRate = null;
let rateUpdatedAt = null;

const els = {
  rateText: document.getElementById("rateText"),
  rateUpdated: document.getElementById("rateUpdated"),
  refreshRateBtn: document.getElementById("refreshRateBtn"),
  paymentJpy: document.getElementById("paymentJpy"),
  paymentDate: document.getElementById("paymentDate"),
  paymentCountry: document.getElementById("paymentCountry"),
  paymentChannel: document.getElementById("paymentChannel"),
  calculateBtn: document.getElementById("calculateBtn"),
  result: document.getElementById("result"),
  cardList: document.getElementById("cardList"),
  promoForm: document.getElementById("promoForm"),
  promoName: document.getElementById("promoName"),
  promoCard: document.getElementById("promoCard"),
  promoStart: document.getElementById("promoStart"),
  promoEnd: document.getElementById("promoEnd"),
  promoCountry: document.getElementById("promoCountry"),
  promoChannel: document.getElementById("promoChannel"),
  promoMinAmount: document.getElementById("promoMinAmount"),
  promoMinCurrency: document.getElementById("promoMinCurrency"),
  promoDiscountPercent: document.getElementById("promoDiscountPercent"),
  promoMaxDiscount: document.getElementById("promoMaxDiscount"),
  promoMaxCurrency: document.getElementById("promoMaxCurrency"),
  promoMaxUses: document.getElementById("promoMaxUses"),
  promoUsedCount: document.getElementById("promoUsedCount"),
  promoTotalLimit: document.getElementById("promoTotalLimit"),
  promoTotalLimitCurrency: document.getElementById("promoTotalLimitCurrency"),
  promoTotalUsed: document.getElementById("promoTotalUsed"),
  promoMonthlyLimit: document.getElementById("promoMonthlyLimit"),
  promoMonthlyLimitCurrency: document.getElementById("promoMonthlyLimitCurrency"),
  promoMonthlyUsed: document.getElementById("promoMonthlyUsed"),
  promoList: document.getElementById("promoList")
};

els.paymentDate.value = todayString();

function formatMoney(value, currency) {
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency,
    maximumFractionDigits: currency === "JPY" ? 0 : 2
  }).format(value);
}

function toJpy(value, currency) {
  if (currency === "JPY") return value;
  if (currency === "USD" && usdToJpyRate) return value * usdToJpyRate;
  return NaN;
}

function toUsd(value, currency) {
  if (currency === "USD") return value;
  if (currency === "JPY" && usdToJpyRate) return value / usdToJpyRate;
  return NaN;
}

async function fetchUsdJpy() {
  els.rateText.textContent = "환율 불러오는 중...";
  try {
    const res = await fetch("https://open.er-api.com/v6/latest/USD");
    const data = await res.json();
    if (data.result !== "success" || !data.rates?.JPY) {
      throw new Error("rate response error");
    }

    usdToJpyRate = data.rates.JPY;
    rateUpdatedAt = new Date();
    els.rateText.textContent = `1 USD = ${usdToJpyRate.toFixed(2)} JPY`;
    els.rateUpdated.textContent = `업데이트: ${rateUpdatedAt.toLocaleString("ko-KR")}`;
  } catch (error) {
    els.rateText.textContent = "환율 조회 실패 (네트워크 확인 필요)";
    els.rateUpdated.textContent = "";
  }
}

function renderCards() {
  els.cardList.innerHTML = "";
  els.promoCard.innerHTML = "";

  state.cards.forEach((card) => {
    const wrapper = document.createElement("div");
    wrapper.className = "item";
    wrapper.innerHTML = `
      <h3>${card.name}</h3>
      <label>
        <input type="checkbox" ${card.enabled ? "checked" : ""} data-card-id="${card.id}" />
        계산에 포함
      </label>
    `;
    els.cardList.appendChild(wrapper);

    const option = document.createElement("option");
    option.value = card.id;
    option.textContent = card.name;
    els.promoCard.appendChild(option);
  });

  els.cardList.querySelectorAll("input[type='checkbox']").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const card = state.cards.find((c) => c.id === checkbox.dataset.cardId);
      if (card) {
        card.enabled = checkbox.checked;
        saveState();
      }
    });
  });
}

function renderPromotions() {
  els.promoList.innerHTML = "";
  if (state.promotions.length === 0) {
    els.promoList.innerHTML = '<p class="small">등록된 행사가 없습니다.</p>';
    return;
  }

  state.promotions.forEach((promo) => {
    const card = state.cards.find((c) => c.id === promo.cardId);
    const wrapper = document.createElement("div");
    wrapper.className = "item";
    wrapper.innerHTML = `
      <h3>${promo.name}</h3>
      <div class="small">
        <span class="tag">${card?.name ?? "미지정 카드"}</span>
        <span class="tag">${promo.startDate} ~ ${promo.endDate}</span>
        <span class="tag">${promo.country}</span>
      </div>
      <p class="small">최소 ${promo.minAmount} ${promo.minCurrency} / 할인 ${promo.discountPercent}% (최대 ${promo.maxDiscount} ${promo.maxCurrency})</p>
      <p class="small">횟수 ${promo.usedCount}/${promo.maxUses}회 사용</p>
      <p class="small">총한도 ${promo.totalLimit ? `${promo.totalUsed ?? 0}/${promo.totalLimit} ${promo.totalLimitCurrency ?? promo.maxCurrency}` : "제한 없음"} / 월한도 ${promo.monthlyLimit ? `${promo.monthlyUsed ?? 0}/${promo.monthlyLimit} ${promo.monthlyLimitCurrency ?? promo.maxCurrency}` : "제한 없음"}</p>
      <div class="actions">
        <button type="button" data-action="use" data-id="${promo.id}">1회 사용 +</button>
        <button type="button" class="danger" data-action="delete" data-id="${promo.id}">삭제</button>
      </div>
    `;
    els.promoList.appendChild(wrapper);
  });

  els.promoList.querySelectorAll("button").forEach((button) => {
    const id = button.dataset.id;
    const action = button.dataset.action;

    button.addEventListener("click", () => {
      const idx = state.promotions.findIndex((p) => p.id === id);
      if (idx < 0) return;

      if (action === "delete") {
        state.promotions.splice(idx, 1);
      } else if (action === "use") {
        const promo = state.promotions[idx];
        promo.usedCount = Math.min(promo.maxUses, promo.usedCount + 1);
      }

      saveState();
      renderPromotions();
    });
  });
}

function addPromotionFromForm(event) {
  event.preventDefault();

  const newPromo = {
    id: crypto.randomUUID(),
    name: els.promoName.value.trim(),
    cardId: els.promoCard.value,
    startDate: els.promoStart.value,
    endDate: els.promoEnd.value,
    country: els.promoCountry.value.trim().toUpperCase(),
    channel: els.promoChannel.value,
    minAmount: Number(els.promoMinAmount.value),
    minCurrency: els.promoMinCurrency.value,
    discountPercent: Number(els.promoDiscountPercent.value),
    maxDiscount: Number(els.promoMaxDiscount.value),
    maxCurrency: els.promoMaxCurrency.value,
    maxUses: Number(els.promoMaxUses.value),
    usedCount: Number(els.promoUsedCount.value),
    totalLimit: els.promoTotalLimit.value ? Number(els.promoTotalLimit.value) : null,
    totalLimitCurrency: els.promoTotalLimitCurrency.value,
    totalUsed: Number(els.promoTotalUsed.value || 0),
    monthlyLimit: els.promoMonthlyLimit.value ? Number(els.promoMonthlyLimit.value) : null,
    monthlyLimitCurrency: els.promoMonthlyLimitCurrency.value,
    monthlyUsed: Number(els.promoMonthlyUsed.value || 0)
  };

  state.promotions.push(newPromo);
  saveState();
  renderPromotions();
  els.promoForm.reset();
  els.promoCountry.value = "JP";
  els.promoChannel.value = "offline";
  els.promoUsedCount.value = "0";
  els.promoTotalLimit.value = "";
  els.promoTotalLimitCurrency.value = "JPY";
  els.promoTotalUsed.value = "0";
  els.promoMonthlyLimit.value = "";
  els.promoMonthlyLimitCurrency.value = "JPY";
  els.promoMonthlyUsed.value = "0";
}

function evaluatePromotion(promo, payment) {
  const base = {
    eligibility: { ok: false, reason: "" },
    cap_before: {
      remaining_uses: Math.max((promo.maxUses ?? 0) - (promo.usedCount ?? 0), 0),
      remaining_total_limit: null,
      remaining_monthly_limit: null
    },
    cap_after: {
      remaining_uses: Math.max((promo.maxUses ?? 0) - (promo.usedCount ?? 0), 0),
      remaining_total_limit: null,
      remaining_monthly_limit: null
    },
    discountJpy: 0,
    discountUsd: NaN
  };

  const totalLimitCurrency = promo.totalLimitCurrency ?? promo.maxCurrency;
  const monthlyLimitCurrency = promo.monthlyLimitCurrency ?? promo.maxCurrency;

  if (promo.totalLimit != null && promo.totalLimit !== "") {
    const remainingTotal = Math.max((promo.totalLimit ?? 0) - (promo.totalUsed ?? 0), 0);
    base.cap_before.remaining_total_limit = { amount: remainingTotal, currency: totalLimitCurrency };
    base.cap_after.remaining_total_limit = { amount: remainingTotal, currency: totalLimitCurrency };
  }

  if (promo.monthlyLimit != null && promo.monthlyLimit !== "") {
    const remainingMonthly = Math.max((promo.monthlyLimit ?? 0) - (promo.monthlyUsed ?? 0), 0);
    base.cap_before.remaining_monthly_limit = { amount: remainingMonthly, currency: monthlyLimitCurrency };
    base.cap_after.remaining_monthly_limit = { amount: remainingMonthly, currency: monthlyLimitCurrency };
  }

  if (payment.date < promo.startDate || payment.date > promo.endDate) {
    return { ...base, eligibility: { ok: false, reason: "기간 조건 불충족" } };
  }
  if (payment.country !== promo.country) {
    return { ...base, eligibility: { ok: false, reason: "국가 조건 불일치" } };
  }
  if (promo.channel !== "both" && payment.channel !== promo.channel) {
    return { ...base, eligibility: { ok: false, reason: "결제 방식 조건 불일치" } };
  }
  if ((promo.usedCount ?? 0) >= (promo.maxUses ?? 0)) {
    return { ...base, eligibility: { ok: false, reason: "횟수 한도 소진" } };
  }

  const paymentInPromoCurrency = promo.minCurrency === "JPY" ? payment.jpy : toUsd(payment.jpy, "JPY");
  if (!Number.isFinite(paymentInPromoCurrency)) {
    return { ...base, eligibility: { ok: false, reason: "환율 정보 없음" } };
  }
  if (paymentInPromoCurrency < promo.minAmount) {
    return { ...base, eligibility: { ok: false, reason: `최소 결제 금액 미달 (${promo.minAmount} ${promo.minCurrency})` } };
  }

  const rawDiscountJpy = payment.jpy * (promo.discountPercent / 100);
  const maxDiscountJpy = toJpy(promo.maxDiscount, promo.maxCurrency);
  if (!Number.isFinite(maxDiscountJpy)) {
    return { ...base, eligibility: { ok: false, reason: "할인 한도 환산 실패" } };
  }

  let discountJpy = Math.min(rawDiscountJpy, maxDiscountJpy);

  if (base.cap_before.remaining_total_limit) {
    const remainJpy = toJpy(base.cap_before.remaining_total_limit.amount, base.cap_before.remaining_total_limit.currency);
    if (!Number.isFinite(remainJpy)) {
      return { ...base, eligibility: { ok: false, reason: "총한도 환산 실패" } };
    }
    if (remainJpy <= 0) {
      return { ...base, eligibility: { ok: false, reason: "총한도 소진" } };
    }
    discountJpy = Math.min(discountJpy, remainJpy);
  }

  if (base.cap_before.remaining_monthly_limit) {
    const remainJpy = toJpy(base.cap_before.remaining_monthly_limit.amount, base.cap_before.remaining_monthly_limit.currency);
    if (!Number.isFinite(remainJpy)) {
      return { ...base, eligibility: { ok: false, reason: "월한도 환산 실패" } };
    }
    if (remainJpy <= 0) {
      return { ...base, eligibility: { ok: false, reason: "월한도 소진" } };
    }
    discountJpy = Math.min(discountJpy, remainJpy);
  }

  base.cap_after.remaining_uses = Math.max(base.cap_before.remaining_uses - 1, 0);

  if (base.cap_before.remaining_total_limit) {
    const discountInTotalCurrency = base.cap_before.remaining_total_limit.currency === "JPY"
      ? discountJpy
      : toUsd(discountJpy, "JPY");
    base.cap_after.remaining_total_limit = {
      ...base.cap_before.remaining_total_limit,
      amount: Math.max(base.cap_before.remaining_total_limit.amount - discountInTotalCurrency, 0)
    };
  }

  if (base.cap_before.remaining_monthly_limit) {
    const discountInMonthlyCurrency = base.cap_before.remaining_monthly_limit.currency === "JPY"
      ? discountJpy
      : toUsd(discountJpy, "JPY");
    base.cap_after.remaining_monthly_limit = {
      ...base.cap_before.remaining_monthly_limit,
      amount: Math.max(base.cap_before.remaining_monthly_limit.amount - discountInMonthlyCurrency, 0)
    };
  }

  return {
    ...base,
    eligibility: { ok: true, reason: "적용 가능" },
    discountJpy,
    discountUsd: toUsd(discountJpy, "JPY")
  };
}

function formatRemainingLimit(limitInfo) {
  if (!limitInfo) return "제한 없음";
  return `${formatMoney(limitInfo.amount, limitInfo.currency)}`;
}

function formatCapState(cap) {
  if (!cap || cap.remaining_uses == null) {
    return "정보 없음";
  }

  return `남은 횟수 ${cap.remaining_uses}회 / 남은 총한도 ${formatRemainingLimit(cap.remaining_total_limit)} / 남은 월한도 ${formatRemainingLimit(cap.remaining_monthly_limit)}`;
}

function calculateBestCard() {
  if (!usdToJpyRate) {
    els.result.innerHTML = '<p class="small">환율을 먼저 불러와 주세요.</p>';
    return;
  }

  const payment = {
    jpy: Number(els.paymentJpy.value),
    date: els.paymentDate.value,
    country: els.paymentCountry.value,
    channel: els.paymentChannel.value
  };

  if (!payment.jpy || payment.jpy <= 0 || !payment.date) {
    els.result.innerHTML = '<p class="small">결제 금액/날짜를 입력해 주세요.</p>';
    return;
  }

  const activeCards = state.cards.filter((c) => c.enabled);
  if (activeCards.length === 0) {
    els.result.innerHTML = '<p class="small">활성화된 카드가 없습니다.</p>';
    return;
  }

  const rows = [];

  for (const card of activeCards) {
    const promos = state.promotions.filter((p) => p.cardId === card.id);
    let best = {
      discountJpy: 0,
      discountUsd: NaN,
      promoName: null,
      eligibility: { ok: false, reason: "적용 가능한 행사 없음" },
      cap_before: { remaining_uses: null, remaining_total_limit: null, remaining_monthly_limit: null },
      cap_after: { remaining_uses: null, remaining_total_limit: null, remaining_monthly_limit: null }
    };

    for (const promo of promos) {
      const result = evaluatePromotion(promo, payment);
      if (result.eligibility.ok && result.discountJpy >= best.discountJpy) {
        best = {
          discountJpy: result.discountJpy,
          discountUsd: result.discountUsd,
          promoName: promo.name,
          eligibility: result.eligibility,
          cap_before: result.cap_before,
          cap_after: result.cap_after
        };
      } else if (!best.eligibility.ok && !result.eligibility.ok) {
        best = { ...best, eligibility: result.eligibility };
      }
    }

    rows.push({ card, ...best });
  }

  rows.sort((a, b) => b.discountJpy - a.discountJpy);

  const listHtml = rows
    .map((row, index) => {
      const rank = index + 1;
      const saveJpy = formatMoney(row.discountJpy, "JPY");
      const saveUsd = Number.isFinite(row.discountUsd) ? ` (${formatMoney(row.discountUsd, "USD")})` : "";
      const expectedPay = formatMoney(Math.max(payment.jpy - row.discountJpy, 0), "JPY");

      return `
        <div class="item">
          <h3>${rank}위: ${row.card.name}</h3>
          <p>예상 할인: <strong>${saveJpy}${saveUsd}</strong></p>
          <p>예상 결제액: <strong>${expectedPay}</strong></p>
          <p class="small">적용 행사: ${row.promoName ?? "없음"}</p>
          <p class="small">eligibility: ${row.eligibility.ok ? "적용 가능" : `적용 불가 (${row.eligibility.reason})`}</p>
          <p class="small">cap_before: ${formatCapState(row.cap_before)}</p>
          <p class="small">cap_after: ${formatCapState(row.cap_after)}</p>
        </div>
      `;
    })
    .join("");

  els.result.innerHTML = `
    <p><strong>결론:</strong> ${rows[0].card.name}가 가장 유리합니다.</p>
    ${listHtml}
  `;
}

els.refreshRateBtn.addEventListener("click", fetchUsdJpy);
els.calculateBtn.addEventListener("click", calculateBestCard);
els.promoForm.addEventListener("submit", addPromotionFromForm);

renderCards();
renderPromotions();
fetchUsdJpy();
saveState();
