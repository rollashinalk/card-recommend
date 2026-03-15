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
      usedCount: 0
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
      usedCount: 0
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
    usedCount: Number(els.promoUsedCount.value)
  };

  state.promotions.push(newPromo);
  saveState();
  renderPromotions();
  els.promoForm.reset();
  els.promoCountry.value = "JP";
  els.promoChannel.value = "offline";
  els.promoUsedCount.value = "0";
}

function evaluatePromotion(promo, payment) {
  const reasons = [];

  if (payment.date < promo.startDate || payment.date > promo.endDate) {
    reasons.push("행사 기간 아님");
    return { ok: false, reasons, discountJpy: 0 };
  }

  if (payment.country !== promo.country) {
    reasons.push("국가 조건 불일치");
    return { ok: false, reasons, discountJpy: 0 };
  }

  if (promo.channel !== "both" && payment.channel !== promo.channel) {
    reasons.push("결제 방식 조건 불일치");
    return { ok: false, reasons, discountJpy: 0 };
  }

  if (promo.usedCount >= promo.maxUses) {
    reasons.push("사용 횟수 소진");
    return { ok: false, reasons, discountJpy: 0 };
  }

  const paymentInPromoCurrency = promo.minCurrency === "JPY"
    ? payment.jpy
    : toUsd(payment.jpy, "JPY");

  if (!Number.isFinite(paymentInPromoCurrency)) {
    reasons.push("환율 정보 없음");
    return { ok: false, reasons, discountJpy: 0 };
  }

  if (paymentInPromoCurrency < promo.minAmount) {
    reasons.push(`최소 결제 금액 미달 (${promo.minAmount} ${promo.minCurrency})`);
    return { ok: false, reasons, discountJpy: 0 };
  }

  const rawDiscountJpy = payment.jpy * (promo.discountPercent / 100);
  const maxDiscountJpy = toJpy(promo.maxDiscount, promo.maxCurrency);

  if (!Number.isFinite(maxDiscountJpy)) {
    reasons.push("할인 한도 환산 실패");
    return { ok: false, reasons, discountJpy: 0 };
  }

  const discountJpy = Math.min(rawDiscountJpy, maxDiscountJpy);
  return {
    ok: true,
    reasons: [
      `할인율 ${promo.discountPercent}% 적용`,
      `건당 최대 ${promo.maxDiscount} ${promo.maxCurrency}`
    ],
    discountJpy,
    discountUsd: toUsd(discountJpy, "JPY")
  };
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
    let best = { discountJpy: 0, reasons: ["적용 가능한 행사 없음"] };

    for (const promo of promos) {
      const result = evaluatePromotion(promo, payment);
      if (result.ok && result.discountJpy > best.discountJpy) {
        best = {
          discountJpy: result.discountJpy,
          discountUsd: result.discountUsd,
          reasons: [promo.name, ...result.reasons]
        };
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
          <p class="small">근거: ${row.reasons.join(" · ")}</p>
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
