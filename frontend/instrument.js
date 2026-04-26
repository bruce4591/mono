const params = new URLSearchParams(window.location.search);
const market = params.get("market") || "US";
const symbol = params.get("symbol") || "SPY";

const title = document.querySelector("#instrumentTitle");
const lastPrice = document.querySelector("#lastPrice");
const changePct = document.querySelector("#changePct");
const turnover = document.querySelector("#turnover");
const volume = document.querySelector("#volume");
const intradayTitle = document.querySelector("#intradayTitle");
const chartTimezone = document.querySelector("#chartTimezone");
const periodTabs = document.querySelector("#periodTabs");
const klineChart = document.querySelector("#klineChart");
const refreshButton = document.querySelector("#refreshButton");
let activeChart = null;
let activeTimezone = "UTC";

const DEFAULT_VISIBLE_CANDLES = {
  "1m": 90,
  "5m": 96,
  "15m": 96,
  "60m": 80,
  "8h": 90,
  "1d": 120,
};

function formatNumber(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
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
  intradayTitle.textContent = `K 线 ${period.label}`;
  renderCandles(klineChart, getVisibleCandles(period));
}

function getVisibleCandles(period) {
  const count = DEFAULT_VISIBLE_CANDLES[period.label] || 96;
  if (period.items.length <= count) return period.items;
  return period.items.slice(-count);
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
  activeChart.applyNewData(data);
  activeChart.createIndicator("MA", true, { id: "candle_pane" });
  activeChart.createIndicator("VOL", false, { height: 82 });
  activeChart.createIndicator("MACD", false, { height: 92 });
}

function renderSnapshot(snapshot) {
  lastPrice.textContent = formatNumber(snapshot.last_price);
  changePct.textContent =
    snapshot.change_pct === null || snapshot.change_pct === undefined
      ? "--"
      : `${Number(snapshot.change_pct).toFixed(2)}%`;
  turnover.textContent = formatTurnover(snapshot.turnover_raw, snapshot.quote_currency);
  volume.textContent = formatVolume(snapshot.volume_raw);
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
  const intradayIntervals = market === "CRYPTO" ? ["1m", "5m", "15m", "8h"] : ["60m"];
  const [instrumentResponse, dailyResponse, ...intradayResponses] = await Promise.all([
    fetch(`/api/instruments/${encodeURIComponent(market)}/${encodeURIComponent(symbol)}`),
    fetch(`/api/bars/daily?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`),
    ...intradayIntervals.map((interval) =>
      fetch(
        `/api/bars/intraday?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(
          symbol,
        )}&interval=${encodeURIComponent(interval)}`,
      ),
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

  title.textContent = `${instrument.symbol}`;
  chartTimezone.textContent = `图表时区 ${activeTimezone} / 原始时间 UTC`;
  renderSnapshot(snapshot);
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

refreshButton.addEventListener("click", loadInstrument);
setInterval(loadLatestSnapshot, 5000);

loadInstrument().catch(() => {
  title.textContent = "读取失败";
  klineChart.innerHTML = '<div class="error">读取图表失败</div>';
});
