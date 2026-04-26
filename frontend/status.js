const healthStatus = document.querySelector("#healthStatus");
const databaseStatus = document.querySelector("#databaseStatus");
const boardStatusList = document.querySelector("#boardStatusList");
const alertList = document.querySelector("#alertList");
const alertMetricList = document.querySelector("#alertMetricList");
const alertRuleList = document.querySelector("#alertRuleList");
const watchlistList = document.querySelector("#watchlistList");
const jobList = document.querySelector("#jobList");
const sourceList = document.querySelector("#sourceList");
const refreshButton = document.querySelector("#refreshButton");

function empty(label) {
  return `<div class="empty">${label}</div>`;
}

function renderBoards(boards) {
  if (!boards.length) {
    boardStatusList.innerHTML = empty("暂无榜单快照");
    return;
  }
  boardStatusList.innerHTML = boards
    .map(
      (board) => `
        <div class="status-row">
          <div>
            <p class="symbol">${board.board_name}</p>
            <p class="name">${board.snapshot_ts_utc || "--"}</p>
          </div>
          <p class="turnover">${board.item_count} 条</p>
        </div>
      `,
    )
    .join("");
}

function renderWatchlists(watchlists) {
  if (!watchlists.length) {
    watchlistList.innerHTML = empty("暂无关注池");
    return;
  }
  watchlistList.innerHTML = watchlists
    .map(
      (watchlist) => `
        <div class="status-row">
          <div>
            <p class="symbol">${watchlist.watchlist_name}</p>
            <p class="name">${watchlist.items.map((item) => item.symbol).join(" / ")}</p>
          </div>
          <p class="turnover">${watchlist.items.length} 个</p>
        </div>
      `,
    )
    .join("");
}

function renderAlerts(events) {
  if (!events.length) {
    alertList.innerHTML = empty("暂无提醒");
    return;
  }
  alertList.innerHTML = events
    .map(
      (event) => `
        <div class="status-row alert-row">
          <div>
            <p class="symbol">${event.symbol} · ${event.rule_name}</p>
            <p class="name">${event.message}</p>
            <p class="name">${event.triggered_at_utc}</p>
          </div>
          <p class="turnover">${event.metric}</p>
        </div>
      `,
    )
    .join("");
}

function renderAlertMetrics(payload) {
  const metrics = payload.metrics || [];
  const chartIndicators = payload.chart_indicators || [];
  if (!metrics.length && !chartIndicators.length) {
    alertMetricList.innerHTML = empty("暂无指标");
    return;
  }
  const metricRows = metrics
    .map(
      (metric) => `
        <div class="status-row">
          <div>
            <p class="symbol">${metric.label} · ${metric.key}</p>
            <p class="name">${metric.description}</p>
          </div>
          <p class="turnover">提醒</p>
        </div>
      `,
    )
    .join("");
  const chartRow = chartIndicators.length
    ? `
        <div class="status-row">
          <div>
            <p class="symbol">K线图指标</p>
            <p class="name">${chartIndicators.join(" / ")}</p>
          </div>
          <p class="turnover">图表</p>
        </div>
      `
    : "";
  alertMetricList.innerHTML = metricRows + chartRow;
}

function renderAlertRules(rules) {
  if (!rules.length) {
    alertRuleList.innerHTML = empty("暂无提醒规则");
    return;
  }
  alertRuleList.innerHTML = rules
    .map(
      (rule) => `
        <div class="status-row">
          <div>
            <p class="symbol">${rule.symbol} · ${rule.name}</p>
            <p class="name">${rule.metric} ${rule.operator} ${rule.threshold}</p>
          </div>
          <p class="turnover">${rule.is_active ? "启用" : "停用"}</p>
        </div>
      `,
    )
    .join("");
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobList.innerHTML = empty("暂无任务记录");
    return;
  }
  jobList.innerHTML = jobs
    .map(
      (job) => `
        <div class="status-row">
          <div>
            <p class="symbol">${job.job_name}</p>
            <p class="name">${job.last_finished_at || job.last_started_at || job.updated_at || "--"}</p>
          </div>
          <p class="turnover">${job.status}</p>
        </div>
      `,
    )
    .join("");
}

function renderSources(sources) {
  if (!sources.length) {
    sourceList.innerHTML = empty("暂无数据源状态");
    return;
  }
  sourceList.innerHTML = sources
    .map(
      (source) => `
        <div class="status-row">
          <div>
            <p class="symbol">${source.source_name}</p>
            <p class="name">${source.last_success_at || source.last_error_at || "--"}</p>
          </div>
          <p class="turnover">${source.status}</p>
        </div>
      `,
    )
    .join("");
}

async function loadStatus() {
  healthStatus.textContent = "读取中";
  try {
    const [
      healthResponse,
      watchlistsResponse,
      jobsResponse,
      alertsResponse,
      alertMetricsResponse,
      alertRulesResponse,
    ] = await Promise.all([
      fetch("/api/health"),
      fetch("/api/watchlists"),
      fetch("/api/jobs"),
      fetch("/api/alerts/events?limit=20"),
      fetch("/api/alerts/metrics"),
      fetch("/api/alerts/rules"),
    ]);
    if (
      !healthResponse.ok ||
      !watchlistsResponse.ok ||
      !jobsResponse.ok ||
      !alertsResponse.ok ||
      !alertMetricsResponse.ok ||
      !alertRulesResponse.ok
    ) {
      throw new Error("status api failed");
    }
    const health = await healthResponse.json();
    const watchlists = await watchlistsResponse.json();
    const jobs = await jobsResponse.json();
    const alerts = await alertsResponse.json();
    const alertMetrics = await alertMetricsResponse.json();
    const alertRules = await alertRulesResponse.json();
    healthStatus.textContent = health.status;
    databaseStatus.textContent = `数据库 ${health.database.writable ? "可读" : "异常"} / ${health.database.journal_mode}`;
    renderBoards(health.latest_boards || []);
    renderAlerts(alerts.events || []);
    renderAlertMetrics(alertMetrics);
    renderAlertRules(alertRules.rules || []);
    renderWatchlists(watchlists.watchlists || []);
    renderJobs(jobs.jobs || []);
    renderSources(jobs.sources || health.sources || []);
  } catch (error) {
    healthStatus.textContent = "读取失败";
    databaseStatus.textContent = "数据库 --";
    boardStatusList.innerHTML = empty("读取榜单状态失败");
    alertList.innerHTML = empty("读取提醒失败");
    alertMetricList.innerHTML = empty("读取指标失败");
    alertRuleList.innerHTML = empty("读取规则失败");
    watchlistList.innerHTML = empty("读取关注池失败");
    jobList.innerHTML = empty("读取任务失败");
    sourceList.innerHTML = empty("读取数据源失败");
  }
}

refreshButton.addEventListener("click", loadStatus);

loadStatus();
