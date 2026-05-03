const params = new URLSearchParams(window.location.search);
const market = params.get("market") || "US";
const symbol = params.get("symbol") || "SPY";

const title = document.querySelector("#instrumentTitle");
const lastPrice = document.querySelector("#lastPrice");
const changePct = document.querySelector("#changePct");
const turnover = document.querySelector("#turnover");
const volume = document.querySelector("#volume");
const fundingRatePanel = document.querySelector("#fundingRatePanel");
const fundingRate = document.querySelector("#fundingRate");
const nextFundingTime = document.querySelector("#nextFundingTime");
const intradayTitle = document.querySelector("#intradayTitle");
const chartTimezone = document.querySelector("#chartTimezone");
const periodTabs = document.querySelector("#periodTabs");
const klineChart = document.querySelector("#klineChart");
const loadMoreBars = document.querySelector("#loadMoreBars");
const chartRangeHint = document.querySelector("#chartRangeHint");
const refreshButton = document.querySelector("#refreshButton");
let activeChart = null;
let activeTimezone = "UTC";
let activePriceTickSize = null;
let activePeriod = null;
let isLoadingOlderBars = false;

const DEFAULT_VISIBLE_CANDLES = {
  "1m": 90,
  "5m": 96,
  "15m": 96,
  "60m": 80,
  "8h": 90,
  "1d": 120,
};

const LOAD_MORE_CANDLES = {
  "1m": 90,
  "5m": 96,
  "15m": 96,
  "60m": 80,
  "8h": 60,
  "1d": 60,
};

function formatRawPrice(value) {
  if (value === null || value === undefined) return "--";
  return String(value);
}

function formatTurnover(value, currency) {
  if (value === null || value === undefined) return "--";
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
  if (value === null || value === undefined) return "--";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return Number(value).toFixed(2);
}

function formatFundingRate(value) {
  if (value === null || value === undefined) return "--";
  return `${Number(value).toFixed(4)}%`;
}

function normalizeChartTimestamp(bar) {
  const raw = bar.bar_start_ts_utc || bar.trade_date;
  if (!raw) return undefined;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return Date.parse(`${raw}T00:00:00Z`);
  return new Date(raw).getTime();
}

function toKLineData(bar) {
  return {
    timestamp: normalizeChartTimestamp(bar),
    open: Number(bar.open),
    high: Number(bar.high),
    low: Number(bar.low),
    close: Number(bar.close),
    volume: Number(bar.volume_raw || 0),
    turnover: Number(bar.turnover_raw || 0),
  };
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
  if (precision === null) return formatRawPrice(value);
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: precision,
    maximumFractionDigits: precision,
  });
}

function applyChartPrecision(chart, priceTickSize) {
  const pricePrecision = pricePrecisionFromTickSize(priceTickSize);
  if (pricePrecision === null) return;
  if (typeof chart.setPriceVolumePrecision === "function") {
    chart.setPriceVolumePrecision(pricePrecision, 2);
  }
}

function chartLibrary() {
  return window.klinecharts || window.KLineCharts;
}

function buildPeriods(daily, intradayPayloads) {
  const periods = [];
  intradayPayloads.forEach((intraday) => {
    if (intraday.items.length) {
      periods.push({
        label: intraday.interval,
        type: "intraday",
        items: intraday.items,
      });
    }
  });
  if (daily.items.length) {
    periods.push({
      label: daily.interval,
      type: "daily",
      items: daily.items,
    });
  }
  return periods;
}

function renderPeriodTabs(periods, selectedLabel, onSelect) {
  periodTabs.innerHTML = `
    <span class="period-label">周期</span>
    ${periods
      .map(
        (period) => `
          <button class="period-tab${period.label === selectedLabel ? " is-active" : ""}" type="button" data-period="${period.label}">
            ${period.label}
          </button>
        `,
      )
      .join("")}
  `;
  periodTabs.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      const period = periods.find((item) => item.label === button.dataset.period);
      if (period) onSelect(period);
    });
  });
}

function renderSelectedPeriod(period) {
  activePeriod = period;
  intradayTitle.textContent = `K 线 ${period.label}`;
  renderCandles(klineChart, getVisibleCandles(period));
  renderRangeControls(period);
}

function getVisibleCandles(period) {
  const count = DEFAULT_VISIBLE_CANDLES[period.label] || 96;
  if (period.items.length <= count) return period.items;
  return period.items.slice(0, count);
}

function renderRangeControls(period) {
  const earliest = period.items[0];
  const latest = period.items[period.items.length - 1];
  const canLoadMore = (period.type === "intraday" || period.type === "daily") && Boolean(earliest);
  loadMoreBars.disabled = !canLoadMore;
  if (!earliest || !latest) {
    chartRangeHint.textContent = "--";
    return;
  }
  chartRangeHint.textContent = `${period.label} ${formatBarTime(earliest)} → ${formatBarTime(latest)}`;
}

function formatBarTime(bar) {
  return bar.bar_start_ts_utc || bar.trade_date || "--";
}

function mergeBars(existing, incoming) {
  const byTime = new Map();
  [...incoming, ...existing].forEach((bar) => {
    const key = bar.bar_start_ts_utc || bar.trade_date;
    if (key) byTime.set(key, bar);
  });
  return Array.from(byTime.values()).sort((left, right) => {
    return normalizeChartTimestamp(left) - normalizeChartTimestamp(right);
  });
}

function renderCandles(container, bars) {
  const data = bars.map(toKLineData).filter((bar) => {
    return (
      Number.isFinite(bar.timestamp) &&
      Number.isFinite(bar.open) &&
      Number.isFinite(bar.high) &&
      Number.isFinite(bar.low) &&
      Number.isFinite(bar.close)
    );
  });

  if (!data.length) {
    container.innerHTML = '<div class="empty">暂无图表数据</div>';
    return;
  }

  const klinecharts = chartLibrary();
  if (!klinecharts || typeof klinecharts.init !== "function") {
    container.innerHTML = '<div class="error">图表库加载失败</div>';
    return;
  }

  if (activeChart && typeof klinecharts.dispose === "function") {
    klinecharts.dispose(container);
    activeChart = null;
  }

  container.replaceChildren();
  activeChart = klinecharts.init(container, {
    locale: "zh-CN",
    timezone: activeTimezone,
    styles: {
      grid: {
        horizontal: { color: "rgba(46, 59, 70, 0.55)" },
        vertical: { color: "rgba(46, 59, 70, 0.55)" },
      },
      candle: {
        bar: {
          upColor: "#0ecb81",
          downColor: "#f6465d",
          noChangeColor: "#8fa3b4",
          upBorderColor: "#0ecb81",
          downBorderColor: "#f6465d",
          noChangeBorderColor: "#8fa3b4",
          upWickColor: "#0ecb81",
          downWickColor: "#f6465d",
          noChangeWickColor: "#8fa3b4",
        },
      },
      indicator: {
        tooltip: {
          text: {
            color: "#8fa3b4",
          },
        },
      },
      xAxis: {
        axisLine: { color: "#2e3b46" },
        tickText: { color: "#8fa3b4" },
      },
      yAxis: {
        axisLine: { color: "#2e3b46" },
        tickText: { color: "#8fa3b4" },
      },
    },
  });
  applyChartPrecision(activeChart, activePriceTickSize);
  activeChart.applyNewData(data);
  setupChartHistoryLoader();
  activeChart.createIndicator("MA", true, { id: "candle_pane" });
  activeChart.createIndicator("VOL", false, { height: 82 });
  activeChart.createIndicator("MACD", false, { height: 92 });
}

function renderSnapshot(snapshot) {
  lastPrice.textContent = formatPrice(snapshot.last_price, activePriceTickSize);
  changePct.textContent =
    snapshot.change_pct === null || snapshot.change_pct === undefined
      ? "--"
      : `${Number(snapshot.change_pct).toFixed(2)}%`;
  turnover.textContent = formatTurnover(snapshot.turnover_raw, snapshot.quote_currency);
  volume.textContent = formatVolume(snapshot.volume_raw);
}

function renderFundingRate(funding) {
  if (!fundingRatePanel || market !== "CRYPTO_FUTURES" || !funding) {
    if (fundingRatePanel) fundingRatePanel.hidden = true;
    return;
  }
  fundingRate.textContent = formatFundingRate(funding.last_funding_rate_pct);
  nextFundingTime.textContent = funding.next_funding_time_utc || "--";
  fundingRatePanel.hidden = false;
}

async function loadLatestSnapshot() {
  const response = await fetch(
    `/api/instruments/${encodeURIComponent(market)}/${encodeURIComponent(symbol)}`,
  );
  if (!response.ok) return;
  const instrument = await response.json();
  renderSnapshot(instrument.latest_snapshot || {});
}

async function loadInstrument() {
  title.textContent = `${market}:${symbol}`;
  const intradayIntervals =
    market === "CRYPTO" || market === "CRYPTO_FUTURES" ? ["1m", "5m", "15m", "8h"] : ["60m"];
  const [instrumentResponse, dailyResponse, ...intradayResponses] = await Promise.all([
    fetch(
      `/api/instruments/${encodeURIComponent(market)}/${encodeURIComponent(symbol)}?include_funding=1`,
    ),
    fetch(`/api/bars/daily?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`),
    ...intradayIntervals.map((interval) =>
      fetchIntradayBars(interval, DEFAULT_VISIBLE_CANDLES[interval] || 96),
    ),
  ]);

  if (!instrumentResponse.ok) {
    title.textContent = "未找到标的";
    return;
  }

  const instrument = await instrumentResponse.json();
  const daily = await dailyResponse.json();
  const intradayPayloads = await Promise.all(
    intradayResponses.map((response) => response.json()),
  );
  const snapshot = instrument.latest_snapshot || {};
  activeTimezone = instrument.timezone || "UTC";
  activePriceTickSize = instrument.extra_meta?.price_tick_size || null;

  title.textContent = `${instrument.symbol}`;
  chartTimezone.textContent = `图表时区 ${activeTimezone} / 原始时间 UTC`;
  renderSnapshot(snapshot);
  renderFundingRate(instrument.funding_rate);
  const periods = buildPeriods(daily, intradayPayloads);
  const selectedPeriod = periods[0];
  if (!selectedPeriod) {
    periodTabs.innerHTML = "";
    intradayTitle.textContent = "K 线 --";
    renderCandles(klineChart, []);
    return;
  }
  function selectPeriod(period) {
    renderPeriodTabs(periods, period.label, selectPeriod);
    renderSelectedPeriod(period);
  }
  selectPeriod(selectedPeriod);
}

async function fetchIntradayBars(interval, limit, beforeTsUtc = null) {
  const query = new URLSearchParams({
    market,
    symbol,
    interval,
    limit: String(limit),
  });
  if (beforeTsUtc) query.set("before_ts_utc", beforeTsUtc);
  return fetch(`/api/bars/intraday?${query.toString()}`);
}

async function fetchDailyBars(limit, beforeTradeDate = null) {
  const query = new URLSearchParams({
    market,
    symbol,
    limit: String(limit),
  });
  if (beforeTradeDate) query.set("before_trade_date", beforeTradeDate);
  return fetch(`/api/bars/daily?${query.toString()}`);
}

async function fetchOlderBars() {
  if (isLoadingOlderBars) return;
  if (
    !activePeriod ||
    !["intraday", "daily"].includes(activePeriod.type) ||
    !activePeriod.items.length
  ) {
    return;
  }
  const earliest = activePeriod.items[0];
  const beforeValue =
    activePeriod.type === "daily" ? earliest.trade_date : earliest.bar_start_ts_utc;
  if (!beforeValue) return;
  isLoadingOlderBars = true;
  loadMoreBars.disabled = true;
  loadMoreBars.textContent = "加载中";
  try {
    const limit = LOAD_MORE_CANDLES[activePeriod.label] || 96;
    const response =
      activePeriod.type === "daily"
        ? await fetchDailyBars(limit, beforeValue)
        : await fetchIntradayBars(activePeriod.label, limit, beforeValue);
    if (!response.ok) return [];
    const payload = await response.json();
    if (!payload.items.length) {
      chartRangeHint.textContent = "没有更早数据";
      return [];
    }
    activePeriod.items = mergeBars(activePeriod.items, payload.items);
    return payload.items;
  } finally {
    isLoadingOlderBars = false;
    loadMoreBars.textContent = "更早";
    loadMoreBars.disabled =
      !activePeriod || !["intraday", "daily"].includes(activePeriod.type);
  }
}

async function loadOlderBars() {
  const incoming = await fetchOlderBars();
  if (!incoming?.length) return;
  renderSelectedPeriod(activePeriod);
}

function setupChartHistoryLoader() {
  if (!activeChart || typeof activeChart.setLoadDataCallback !== "function") return;
  activeChart.setLoadDataCallback(async (params) => {
    const complete = typeof params.callback === "function" ? params.callback : () => {};
    if (!activePeriod || !["intraday", "daily"].includes(activePeriod.type)) {
      complete([], false);
      return;
    }
    const earliest = activePeriod.items[0];
    const earliestTimestamp = normalizeChartTimestamp(earliest);
    const boundaryTimestamp = params.data?.timestamp;
    const isLeftBoundary =
      params.type === "forward" ||
      !Number.isFinite(boundaryTimestamp) ||
      boundaryTimestamp <= earliestTimestamp;

    if (!isLeftBoundary) {
      complete([], false);
      return;
    }

    try {
      const incoming = await fetchOlderBars();
      const data = (incoming || []).map(toKLineData).filter((bar) => {
        return (
          Number.isFinite(bar.timestamp) &&
          Number.isFinite(bar.open) &&
          Number.isFinite(bar.high) &&
          Number.isFinite(bar.low) &&
          Number.isFinite(bar.close)
        );
      });
      complete(data, data.length > 0);
      if (data.length) renderRangeControls(activePeriod);
    } catch {
      complete([], true);
    }
  });
}

refreshButton.addEventListener("click", loadInstrument);
loadMoreBars.addEventListener("click", loadOlderBars);
setInterval(loadLatestSnapshot, 5000);

loadInstrument().catch(() => {
  title.textContent = "读取失败";
  klineChart.innerHTML = '<div class="error">读取图表失败</div>';
});
