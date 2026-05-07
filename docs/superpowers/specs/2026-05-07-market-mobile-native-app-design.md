# Market Mobile Native App Design

## 目标

把当前 `apps/market-mobile` 从“Expo App 内嵌 WebView”改造成真正的 Expo
原生行情提醒 App。新版本不保留 WebView 兜底路径，所有核心体验都由 React
Native 页面、原生通知和服务端 API 承担。

目标体验：

- 手机首次打开就是原生首页，不再加载 `frontend/index.html`。
- 排行榜、详情页、K 线、提醒管理都使用原生 React Native UI。
- App 在线时使用 SSE 长连接接收提醒。
- App 后台、锁屏或被系统杀掉时使用个推系统级 Push。
- App 回前台时补拉漏掉的提醒事件。
- 后端继续作为唯一数据源，App 不直接访问 PostgreSQL 或 Parquet。

## 当前基线

当前移动端已经有以下能力：

- `apps/market-mobile/App.tsx` 使用 `react-native-webview` 打开 `WEB_BASE_URL`。
- `src/notifications.ts` 负责 Expo Push Token 和后端注册。
- `src/getui.ts` 负责个推 Client ID 初始化和注册。
- `src/onlineAlerts.ts` 在 App 前台通过 SSE 接收提醒。
- `src/alertEvents.ts` 从后端补拉提醒事件并做本地通知展示。
- 后端已有移动设备、提醒规则、提醒事件、投递状态、SSE 和推送 worker。

这说明通知通道已经跑通。下一阶段的重点不是重新做推送，而是移除 WebView
依赖，把页面、数据请求、缓存、错误状态和手机适配全部搬到 Expo 原生层。

## 核心决策

### 1. 不保留 WebView

新版本移除 `react-native-webview` 作为业务页面承载方式。

原因：

- WebView 继承网页布局问题，手机端适配成本高。
- WebView 依赖 HTML、JS、CSS、CDN 加载顺序，容易出现白屏或体感慢。
- 原生通知点击、前后台状态、页面跳转和数据补拉更适合由 App 路由控制。
- 后续自定义提醒、Agent 调用和本地状态管理需要更明确的数据边界。

移除范围：

- `App.tsx` 不再渲染 `<WebView />`。
- `notificationUrlFromData` 不再拼网页 URL，而是转换成原生路由目标。
- `react-native-webview` 依赖在迁移完成后从 `package.json` 移除。
- `plugins/withCleartextTraffic.js` 和 Android 明文网页访问配置在公网 HTTPS
  稳定后移除。

### 2. App 只调用后端 API

App 不直接读数据库，也不解析 Parquet。所有行情、提醒、规则、设备状态都通过
后端 API 访问。

移动端 API 要按页面聚合返回数据，减少手机端请求数量：

- 首页/排行榜：`GET /api/mobile/home`
- 股票详情：`GET /api/mobile/instrument-detail?market=HK&symbol=09988`
- 提醒事件：`GET /api/mobile/alert-events`
- 提醒规则：`GET /api/mobile/alert-rules`
- 创建提醒规则：`POST /api/mobile/alert-rules`
- 修改提醒规则：`PATCH /api/mobile/alert-rules/{id}`
- 注册设备：沿用当前设备注册接口
- 在线提醒：沿用当前 SSE 接口

### 3. 在线和后台提醒分层

提醒通道按 App 状态分层：

- 前台：保持 SSE 长连接，收到服务端事件后展示本地通知或页面内提醒。
- 回前台：立即调用补拉接口，按 `event_id` 去重并 ack。
- 后台/锁屏/杀进程：服务端通过个推 Push 投递系统通知。
- 点击通知：打开原生详情页或提醒详情页，再补拉最新事件。

App 不做后台短轮询。后台轮询既耗电，也容易被国产 Android 系统限制。

### 4. 原生页面优先级

第一版原生 App 只做高频核心路径：

1. 首页/排行榜
2. 股票详情
3. K 线图
4. 提醒事件列表
5. 提醒规则创建/编辑
6. 设置/调试页

后台管理类页面、低频诊断页面可以先不迁移到 App，保留在服务端网页或内部工具中。

## 原生 App 架构

```text
apps/market-mobile/
  App.tsx
  src/
    api/
      client.ts
      types.ts
      market.ts
      alerts.ts
      device.ts
    app/
      AppRoot.tsx
      navigation.ts
    screens/
      HomeScreen.tsx
      InstrumentDetailScreen.tsx
      AlertEventsScreen.tsx
      AlertRulesScreen.tsx
      AlertRuleEditorScreen.tsx
      SettingsScreen.tsx
    components/
      AppShell.tsx
      MarketList.tsx
      MarketListItem.tsx
      PriceChange.tsx
      DataTimeBadge.tsx
      NativeKLineChart.tsx
      EmptyState.tsx
      ErrorState.tsx
      LoadingState.tsx
    alerts/
      alertRuntime.ts
      notificationRouter.ts
    storage/
      preferences.ts
    theme/
      colors.ts
      spacing.ts
      typography.ts
```

边界：

- `api/` 只负责请求、类型、错误转换，不包含 UI。
- `screens/` 负责页面状态和用户操作。
- `components/` 只负责可复用 UI。
- `alerts/` 负责通知点击路由、SSE、补拉和 ack 的组合逻辑。
- `storage/` 只保存轻量偏好，例如最近市场、调试开关、最后打开的 symbol。
- `theme/` 固化颜色、字号和间距，避免页面各自写一套样式。

## 页面设计

### 首页/排行榜

首页显示多个市场标签：

- HK
- US
- ETF
- Crypto Spot
- Crypto Futures

每个列表项显示：

- 名称和 symbol
- 最新价
- 涨跌幅
- 成交额或成交量
- 排名变化
- 数据时间

点击列表项进入原生详情页。

首页请求策略：

- 首次打开立即请求。
- 下拉刷新强制请求。
- 回前台时如果缓存超过 60 秒，自动刷新。
- 60 秒内重复进入不重新请求，直接显示缓存。

### 股票详情页

详情页顶部显示：

- symbol 和名称
- 当前价格
- 涨跌幅
- 最新快照时间
- 行情来源

中部显示原生 K 线图：

- 第一版支持日线。
- 第二版支持 `1m`、`5m`、`15m`、`8h`、`1d` 切换。
- 图表先用 `react-native-svg` 实现基础蜡烛图、成交量和均线。
- 如果后续需要复杂手势和性能优化，再评估 Skia 或专用图表库。

底部显示：

- OHLC
- 成交量/成交额
- 可用周期
- 数据更新时间

详情页请求策略：

- 进入页面请求聚合接口。
- 60 秒内重复打开同一个 symbol 使用缓存。
- 用户手动刷新时强制请求。
- 请求失败时保留旧数据并显示错误提示。

### 提醒事件列表

显示最近提醒事件：

- 标题
- 触发原因
- symbol
- 触发价格/指标值
- 触发时间
- 投递状态

点击事件进入对应股票详情页，并高亮触发信息。

### 提醒规则管理

提醒规则列表显示：

- 规则名称
- symbol 或 watchlist
- 指标
- 条件
- 冷却时间
- 是否启用
- 最近触发时间

规则编辑支持：

- 价格高于/低于
- 涨跌幅阈值
- 成交额/成交量阈值
- 自定义指标条件
- 冷却时间
- 合并窗口

第一版只做用户可理解的表单，不在手机端写复杂表达式编辑器。复杂自定义指标由后端和 Agent API 创建，App 负责展示和启停。

## 推送和前后台运行

### 注册流程

App 启动后：

1. 初始化个推 SDK。
2. 获取个推 CID。
3. 请求 Android 通知权限。
4. 获取 Expo Push Token。
5. 调用后端注册设备接口。
6. 注册成功后启动 SSE。
7. 立即补拉一次漏掉的事件。

注册失败时：

- 不阻塞 App 主页面。
- 设置页显示当前通道状态。
- 下次启动和回前台时重试。

### SSE 流程

只在前台保持 SSE：

1. App active。
2. 已有可用 push token 或 getui CID。
3. 连接 `/api/mobile/alert-stream`。
4. 收到 alert event 后展示本地通知或页面内提示。
5. 更新本地 `latestSeenAlertEventId`。
6. 调用 ack 接口。
7. 断线后 3 秒重连。

App 进入后台时关闭 SSE。

### 后台 Push 流程

服务端负责：

1. 定时或实时评估提醒规则。
2. 写入 `mobile_alert_event`。
3. 写入 `mobile_alert_delivery`。
4. 优先通过个推投递。
5. 回写 delivery status。

App 收到/点击通知后：

1. 根据通知 payload 转换成原生路由。
2. 打开对应股票详情或提醒事件页。
3. 补拉漏掉事件。
4. ack 最新事件。

## 后端 API 设计

### `GET /api/mobile/home`

返回首页所需的多个榜单摘要。

响应结构：

```json
{
  "server_time": "2026-05-07T12:00:00Z",
  "boards": [
    {
      "key": "HK_TURNOVER_TOP50",
      "title": "港股成交额",
      "market": "HK",
      "data_time": "2026-05-07T11:59:00Z",
      "items": [
        {
          "market": "HK",
          "symbol": "09988",
          "name": "Alibaba",
          "last_price": 123.45,
          "change_pct": 1.23,
          "turnover": 1234567890,
          "rank": 1,
          "rank_change": 2,
          "data_time": "2026-05-07T11:59:00Z"
        }
      ]
    }
  ]
}
```

### `GET /api/mobile/instrument-detail`

返回详情页完整数据。

响应结构：

```json
{
  "instrument": {
    "market": "HK",
    "symbol": "09988",
    "name": "Alibaba",
    "asset_class": "stock"
  },
  "snapshot": {
    "last_price": 123.45,
    "change_pct": 1.23,
    "volume": 12345678,
    "turnover": 1234567890,
    "data_time": "2026-05-07T11:59:00Z",
    "source": "akshare"
  },
  "periods": ["1d", "1m", "5m", "15m", "8h"],
  "bars": [
    {
      "time": "2026-05-07",
      "open": 120.1,
      "high": 124.2,
      "low": 119.8,
      "close": 123.45,
      "volume": 12345678,
      "turnover": 1234567890
    }
  ]
}
```

### Alert APIs

继续复用当前提醒表和 worker，但为 App 提供移动端形状的响应：

- `GET /api/mobile/alert-events`
- `POST /api/mobile/alert-events/ack`
- `GET /api/mobile/alert-rules`
- `POST /api/mobile/alert-rules`
- `PATCH /api/mobile/alert-rules/{id}`
- `GET /api/mobile/debug/push-device`

## 缓存策略

App 内缓存以页面为单位：

- 首页榜单：60 秒内复用。
- 股票详情：同一 `market + symbol + period` 60 秒内复用。
- 提醒事件：SSE 推来的事件立即合并；回前台补拉。
- 提醒规则：编辑成功后立即更新本地缓存并后台刷新。

缓存失败策略：

- 有旧数据时显示旧数据和错误提示。
- 没有旧数据时显示错误页。
- 用户可以手动重试。

## UI 原则

这是行情工具，不做营销页。

原则：

- 信息密度高，但层级清楚。
- 首页列表适合快速扫描。
- 详情页优先展示价格、涨跌、数据时间和图表。
- 所有数据都显示“数据时间”，避免用户误判行情新旧。
- 按钮、标签、切换器都使用稳定尺寸，避免移动端布局跳动。
- 暗色主题为默认，减少长时间看盘疲劳。

## Agent 和自定义指标

自定义指标继续在服务端计算和存储。

Agent 能力通过后端 API 暴露：

- 创建指标定义。
- 创建提醒规则。
- 查询指标历史。
- 查询提醒触发历史。
- 查询 symbol 历史行情。

Agent 不直接写数据库表。所有写操作必须经过服务端校验、审计和版本记录。

App 对 Agent 创建的规则：

- 可以展示。
- 可以启停。
- 可以删除或修改基础参数。
- 不在第一版 App 内编辑复杂表达式。

## 错误处理

App 层错误：

- 网络失败：显示重试入口。
- API 非 2xx：显示后端错误信息。
- 数据为空：显示空状态，不显示白屏。
- 推送注册失败：不阻塞行情浏览，在设置页显示状态。
- SSE 断开：前台自动重连，后台不重连。

后端错误：

- 移动端聚合接口失败时返回明确错误码。
- 推送 worker 失败要写入 delivery status。
- Alert event 已创建但推送失败时，App 回前台仍能补拉。

## 测试策略

移动端：

- TypeScript typecheck。
- API client 单元测试。
- SSE chunk parser 测试。
- notification payload 到原生路由的转换测试。
- K 线数据归一化测试。

后端：

- 移动端聚合接口测试。
- 提醒规则 CRUD 测试。
- 回前台补拉和 ack 测试。
- 推送 delivery status 回写测试。
- Agent 创建规则审计测试。

手工验证：

- Android 首次安装通知权限。
- 个推 CID 注册。
- 前台 SSE 收消息。
- 后台 Push 收消息。
- 点击通知进入对应详情页。
- 回前台补拉漏掉消息。
- HK/US 排行榜进入详情页能看到数据时间和 K 线。

## 分阶段交付

### Phase 1: 原生 App 骨架

- 移除 WebView 主入口。
- 建原生导航、主题、基础组件。
- 建 API client 和缓存层。
- 保留现有推送注册代码，但接入新 App root。

### Phase 2: 原生行情页面

- 首页/排行榜原生化。
- 股票详情原生化。
- 原生 K 线第一版。
- 所有页面显示数据时间。

### Phase 3: 原生提醒页面

- 提醒事件列表。
- 提醒规则列表。
- 提醒规则创建/编辑。
- 通知点击原生跳转。

### Phase 4: 后台稳定和调试

- 设置页显示推送通道状态。
- SSE 状态展示。
- 后端 delivery debug。
- Android 电池优化引导。

### Phase 5: 删除 WebView 遗留

- 删除 `react-native-webview` 依赖。
- 删除明文 WebView 网络配置。
- 删除 WebView 专用错误页和 URL 路由逻辑。
- APK 重新打包验证。

## 验收标准

新版本满足以下条件才算完成：

- App 首屏不是 WebView。
- 卸载 `react-native-webview` 后仍能正常运行。
- 首页、详情、提醒核心流程全部原生可用。
- 通知点击不再打开网页 URL，而是进入原生页面。
- 前台 SSE 和后台个推 Push 都可用。
- 回前台补拉不会重复弹通知。
- HK/US/ETF/Crypto 详情页都显示数据时间。
- Android 真机 APK 可安装并完成手工验证。
