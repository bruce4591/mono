const boards = {
  ETF_FOCUS20: "ETF",
  CRYPTO_TURNOVER_TOP50: "Crypto",
};

let activeBoard = "ETF_FOCUS20";

const boardName = document.querySelector("#boardName");
const snapshotTime = document.querySelector("#snapshotTime");
const boardList = document.querySelector("#boardList");
const refreshButton = document.querySelector("#refreshButton");
const tabs = Array.from(document.querySelectorAll(".tab"));

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

function formatPrice(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function formatRankChange(value) {
  if (value === null || value === undefined) return '<span class="rank-change is-new">新入</span>';
  if (value > 0) return `<span class="rank-change is-up">上期 ↑${value}</span>`;
  if (value < 0) return `<span class="rank-change is-down">上期 ↓${Math.abs(value)}</span>`;
  return '<span class="rank-change">上期 持平</span>';
}

function renderBoard(payload) {
  boardName.textContent = payload.board_name;
  snapshotTime.textContent = payload.snapshot_ts_utc || "--";
  if (!payload.items.length) {
    boardList.innerHTML = '<div class="empty">暂无榜单数据</div>';
    return;
  }

  boardList.innerHTML = payload.items
    .map((item) => {
      const change = Number(item.change_pct || 0);
      const changeClass = change < 0 ? "change is-down" : "change";
      const sign = change > 0 ? "+" : "";
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
            <p class="price">价 ${formatPrice(item.last_price)}</p>
            <p class="turnover">24h额 ${formatTurnover(item.turnover_raw, item.quote_currency)}</p>
            <p class="name">24h量 ${formatVolume(item.volume_raw)}</p>
            <p class="${changeClass}">24h ${sign}${change.toFixed(2)}%</p>
          </div>
        </a>
      `;
    })
    .join("");
}

async function loadBoard(board) {
  activeBoard = board;
  tabs.forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.board === activeBoard);
  });
  boardList.innerHTML = '<div class="empty">加载中</div>';
  try {
    const response = await fetch(`/api/boards/${encodeURIComponent(board)}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderBoard(await response.json());
  } catch (error) {
    boardName.textContent = boards[board] || board;
    snapshotTime.textContent = "--";
    boardList.innerHTML = '<div class="error">读取榜单失败</div>';
  }
}

tabs.forEach((tab) => {
  tab.addEventListener("click", () => loadBoard(tab.dataset.board));
});

refreshButton.addEventListener("click", () => loadBoard(activeBoard));

loadBoard(activeBoard);
