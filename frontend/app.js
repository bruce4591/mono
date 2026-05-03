const CRYPTO_BOARDS = {
  CRYPTO_TURNOVER_TOP50: "Crypto",
  CRYPTO_FUTURES_TURNOVER_TOP50: "Futures",
  CRYPTO_FUTURES_TRADFI_TURNOVER_TOP50: "TradeFi",
};

const BOARD_LABELS = {
  ETF_FOCUS20: "ETF",
  ...CRYPTO_BOARDS,
};

let activeBoard = "ETF_FOCUS20";
const previousPrices = new Map();

const boardName = document.querySelector("#boardName");
const snapshotTime = document.querySelector("#snapshotTime");
const boardList = document.querySelector("#boardList");
const refreshButton = document.querySelector("#refreshButton");
const sectionTabs = Array.from(document.querySelectorAll(".tab[data-section]"));
const cryptoSubtabs = document.querySelector("#cryptoSubtabs");
const cryptoBoardTabs = Array.from(document.querySelectorAll("#cryptoSubtabs [data-board]"));

function formatTurnover(value, currency) {
  const abs = Math.abs(value);
  let scaled = value;
  let unit = "";
  if (abs >= 1_000_000_000) {
    scaled = value / 1_000_000_000;
    unit = "B";
  } else if (abs >= 1_000_000) {
    scaled = value / 1_000_000;
    unit = "M";
  }
  return `${scaled.toFixed(2)}${unit} ${currency}`;
}

function formatVolume(value) {
  const abs = Math.abs(value || 0);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return Number(value || 0).toFixed(2);
}

function pricePrecisionFromTickSize(tickSize) {
  if (tickSize === null || tickSize === undefined) return null;
  const normalized = String(tickSize).trim();
  if (!normalized || Number(normalized) <= 0) return null;
  if (normalized.toLowerCase().includes("e-")) {
    const precision = Math.ceil(Math.abs(Math.log10(Number(normalized))));
    return Number.isFinite(precision) ? precision : null;
  }
  return (normalized.split(".")[1] || "").replace(/0+$/, "").length;
}

function formatPrice(value, tickSize = null) {
  if (value === null || value === undefined) return "--";
  const precision = pricePrecisionFromTickSize(tickSize);
  if (precision === null) {
    return String(value);
  }
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

function formatPriceDirection(item) {
  if (item.last_price === null || item.last_price === undefined) return "";
  const key = `${item.market}:${item.symbol}`;
  const price = Number(item.last_price);
  const previous = previousPrices.get(key);
  previousPrices.set(key, price);
  if (previous === undefined || previous === price) return "";
  if (price > previous) return '<span class="price-direction is-up">↑</span>';
  return '<span class="price-direction is-down">↓</span>';
}

function formatRankChange(value) {
  if (value === null || value === undefined) return '<span class="rank-change is-new">新入</span>';
  if (value > 0) return `<span class="rank-change is-up">上期 ↑${value}</span>`;
  if (value < 0) return `<span class="rank-change is-down">上期 ↓${Math.abs(value)}</span>`;
  return '<span class="rank-change">上期 持平</span>';
}

function formatSnapshotMeta(payload) {
  const current = payload.snapshot_ts_utc || "--";
  if (!payload.previous_snapshot_ts_utc) return current;
  return `${current} · 排名较上期 ${payload.previous_snapshot_ts_utc}`;
}

function renderBoard(payload) {
  boardName.textContent = payload.board_name;
  snapshotTime.textContent = formatSnapshotMeta(payload);
  if (!payload.items.length) {
    boardList.innerHTML = '<div class="empty">暂无榜单数据</div>';
    return;
  }

  boardList.innerHTML = payload.items
    .map((item) => {
      const change = Number(item.change_pct || 0);
      const changeClass = change < 0 ? "change is-down" : "change";
      const sign = change > 0 ? "+" : "";
      const priceDirection = formatPriceDirection(item);
      return `
        <a class="row" href="/instrument.html?market=${encodeURIComponent(item.market)}&symbol=${encodeURIComponent(item.symbol)}">
          <div class="rank-box">
            <div class="rank">${item.rank}</div>
            ${formatRankChange(item.rank_change)}
          </div>
          <div>
            <p class="symbol">${item.symbol}</p>
            <p class="name">${item.display_name}</p>
          </div>
          <div class="metrics">
            <p class="price">${formatPrice(item.last_price, item.price_tick_size)} ${priceDirection}</p>
            <p class="turnover">${formatTurnover(item.turnover_raw, item.quote_currency)}</p>
            <p class="name">24h量 ${formatVolume(item.volume_raw)}</p>
            <p class="${changeClass}">24h ${sign}${change.toFixed(2)}%</p>
          </div>
        </a>
      `;
    })
    .join("");
}

function syncNavigation(board) {
  const isCryptoBoard = Object.prototype.hasOwnProperty.call(CRYPTO_BOARDS, board);
  sectionTabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.section === (isCryptoBoard ? "CRYPTO" : "ETF"));
  });
  cryptoSubtabs.classList.toggle("is-hidden", !isCryptoBoard);
  cryptoBoardTabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.board === board);
  });
}

async function loadBoard(board, options = {}) {
  activeBoard = board;
  syncNavigation(board);
  if (!options.silent) boardList.innerHTML = '<div class="empty">加载中</div>';
  try {
    const response = await fetch(`/api/boards/${encodeURIComponent(board)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderBoard(await response.json());
  } catch (error) {
    boardName.textContent = BOARD_LABELS[board] || board;
    snapshotTime.textContent = "--";
    boardList.innerHTML = '<div class="error">读取榜单失败</div>';
  }
}

sectionTabs.forEach((tab) => {
  tab.addEventListener("click", () => loadBoard(tab.dataset.board));
});

cryptoBoardTabs.forEach((tab) => {
  tab.addEventListener("click", () => loadBoard(tab.dataset.board));
});

refreshButton.addEventListener("click", () => loadBoard(activeBoard));

loadBoard(activeBoard);
setInterval(() => loadBoard(activeBoard, { silent: true }), 30000);
