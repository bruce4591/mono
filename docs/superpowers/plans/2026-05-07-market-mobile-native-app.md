# Market Mobile Native App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `apps/market-mobile` 从 WebView 壳改造成不依赖 WebView 的 Expo 原生行情提醒 App。

**Architecture:** App 使用 React Native 原生页面承载首页、详情、K 线和提醒管理；通过移动端聚合 API 获取数据；前台使用 SSE，后台使用个推 Push，回前台补拉事件。WebView 依赖在迁移完成后删除。

**Tech Stack:** Expo SDK 55, React Native 0.83, TypeScript, Expo Notifications, 个推 Android SDK, PostgreSQL-backed market API, SSE, native React Native UI.

---

## 文件结构

新增或调整以下文件：

- Modify: `apps/market-mobile/App.tsx`
  - 替换 WebView root，挂载原生 AppRoot。
- Create: `apps/market-mobile/src/app/AppRoot.tsx`
  - App 总入口，管理导航、注册推送、SSE、回前台补拉。
- Create: `apps/market-mobile/src/app/navigation.ts`
  - 轻量路由类型和页面状态。第一版不用额外导航依赖，避免迁移期扩大依赖面。
- Create: `apps/market-mobile/src/api/client.ts`
  - 统一 fetch、错误处理、JSON 解析、超时。
- Create: `apps/market-mobile/src/api/types.ts`
  - 移动端 API 响应类型。
- Create: `apps/market-mobile/src/api/market.ts`
  - 首页和详情接口。
- Create: `apps/market-mobile/src/api/alerts.ts`
  - 提醒事件和规则接口。
- Create: `apps/market-mobile/src/cache/queryCache.ts`
  - 简单 TTL 缓存，避免 60 秒内重复拉同一数据。
- Create: `apps/market-mobile/src/screens/HomeScreen.tsx`
  - 原生首页/排行榜。
- Create: `apps/market-mobile/src/screens/InstrumentDetailScreen.tsx`
  - 原生详情页。
- Create: `apps/market-mobile/src/screens/AlertEventsScreen.tsx`
  - 提醒事件列表。
- Create: `apps/market-mobile/src/screens/AlertRulesScreen.tsx`
  - 提醒规则列表。
- Create: `apps/market-mobile/src/screens/SettingsScreen.tsx`
  - 推送、SSE、后端连接状态。
- Create: `apps/market-mobile/src/components/NativeKLineChart.tsx`
  - 第一版原生 K 线。初始版本用 `View` 和 `StyleSheet` 绘制简化蜡烛，后续再引入图表依赖。
- Create: `apps/market-mobile/src/components/*.tsx`
  - 加载、空状态、错误状态、价格涨跌、数据时间 badge、列表项。
- Create: `apps/market-mobile/src/alerts/notificationRouter.ts`
  - 通知 payload 转原生路由。
- Modify: `apps/market-mobile/src/onlineAlerts.ts`
  - 保留 SSE，但把事件派发给原生 runtime。
- Modify: `apps/market-mobile/package.json`
  - 迁移完成后删除 `react-native-webview`。
- Modify: `src/market/api.py`
  - 增加移动端聚合接口。
- Modify: `tests/test_api.py`
  - 增加移动端聚合接口测试。

## Task 1: 建立可编译的原生 AppRoot

**Files:**
- Create: `apps/market-mobile/src/app/navigation.ts`
- Create: `apps/market-mobile/src/app/AppRoot.tsx`
- Modify: `apps/market-mobile/App.tsx`

- [ ] **Step 1: 定义原生路由类型**

Create `apps/market-mobile/src/app/navigation.ts`:

```ts
export type HomeRoute = { name: "home" };
export type InstrumentRoute = {
  name: "instrument";
  market: string;
  symbol: string;
};
export type AlertEventsRoute = { name: "alertEvents" };
export type AlertRulesRoute = { name: "alertRules" };
export type SettingsRoute = { name: "settings" };

export type AppRoute =
  | HomeRoute
  | InstrumentRoute
  | AlertEventsRoute
  | AlertRulesRoute
  | SettingsRoute;

export const homeRoute: HomeRoute = { name: "home" };
```

- [ ] **Step 2: 创建 AppRoot**

Create `apps/market-mobile/src/app/AppRoot.tsx`:

```tsx
import React, { useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";

export function AppRoot() {
  const [route, setRoute] = useState<AppRoute>(homeRoute);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      <Text style={styles.title}>Market</Text>
      <Text style={styles.subtitle}>原生 App 正在接管页面，当前路由：{route.name}</Text>
      <View style={styles.actions}>
        <Pressable style={styles.button} onPress={() => setRoute(homeRoute)}>
          <Text style={styles.buttonText}>首页</Text>
        </Pressable>
        <Pressable style={styles.button} onPress={() => setRoute({ name: "alertEvents" })}>
          <Text style={styles.buttonText}>提醒</Text>
        </Pressable>
        <Pressable style={styles.button} onPress={() => setRoute({ name: "settings" })}>
          <Text style={styles.buttonText}>设置</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 56,
    backgroundColor: "#071113"
  },
  title: {
    color: "#f8fafc",
    fontSize: 26,
    fontWeight: "700"
  },
  subtitle: {
    marginTop: 10,
    color: "#9fb2b7",
    fontSize: 15,
    lineHeight: 22
  },
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 24
  },
  button: {
    borderRadius: 8,
    backgroundColor: "#123638",
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  buttonText: {
    color: "#dff8f5",
    fontSize: 15,
    fontWeight: "600"
  }
});
```

- [ ] **Step 3: 替换 App.tsx 入口**

Modify `apps/market-mobile/App.tsx` to:

```tsx
import React from "react";

import { AppRoot } from "./src/app/AppRoot";

export default function App() {
  return <AppRoot />;
}
```

- [ ] **Step 4: 运行 typecheck**

Run:

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: exit code 0. This task must not commit a broken intermediate state.

- [ ] **Step 5: Commit**

```bash
git add apps/market-mobile/App.tsx apps/market-mobile/src/app/navigation.ts apps/market-mobile/src/app/AppRoot.tsx
git commit -m "Add native mobile app root"
```

## Task 2: 拆出原生页面占位和基础组件

**Files:**
- Modify: `apps/market-mobile/src/app/AppRoot.tsx`
- Create: `apps/market-mobile/src/screens/HomeScreen.tsx`
- Create: `apps/market-mobile/src/screens/InstrumentDetailScreen.tsx`
- Create: `apps/market-mobile/src/screens/AlertEventsScreen.tsx`
- Create: `apps/market-mobile/src/screens/AlertRulesScreen.tsx`
- Create: `apps/market-mobile/src/screens/SettingsScreen.tsx`
- Create: `apps/market-mobile/src/components/LoadingState.tsx`
- Create: `apps/market-mobile/src/components/ErrorState.tsx`
- Create: `apps/market-mobile/src/components/EmptyState.tsx`

- [ ] **Step 1: 创建通用状态组件**

Create `LoadingState.tsx`, `ErrorState.tsx`, and `EmptyState.tsx` with this shape:

```tsx
import React from "react";
import { StyleSheet, Text, View } from "react-native";

export function LoadingState({ label = "加载中" }: { label?: string }) {
  return (
    <View style={styles.center}>
      <Text style={styles.text}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  text: {
    color: "#d6e2e4",
    fontSize: 15
  }
});
```

Use the same visual structure for `ErrorState` and `EmptyState`, with props for title/message/retry where needed.

- [ ] **Step 2: 创建页面占位**

Each screen exports a named component and accepts `navigate`. `InstrumentDetailScreen` also accepts `route`.

Example for `HomeScreen.tsx`:

```tsx
import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";

export function HomeScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  return (
    <View style={styles.root}>
      <Text style={styles.title}>Market</Text>
      <Pressable onPress={() => navigate({ name: "settings" })}>
        <Text style={styles.link}>设置</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: "#071113"
  },
  title: {
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  link: {
    marginTop: 16,
    color: "#5eead4",
    fontSize: 16
  }
});
```

- [ ] **Step 3: AppRoot 改为引用 screens**

Modify `apps/market-mobile/src/app/AppRoot.tsx` so it imports and renders the new screen modules:

```tsx
import React, { useState } from "react";
import { StatusBar, StyleSheet, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";
import { AlertEventsScreen } from "../screens/AlertEventsScreen";
import { AlertRulesScreen } from "../screens/AlertRulesScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { InstrumentDetailScreen } from "../screens/InstrumentDetailScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export function AppRoot() {
  const [route, setRoute] = useState<AppRoute>(homeRoute);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      {route.name === "home" ? <HomeScreen navigate={setRoute} /> : null}
      {route.name === "instrument" ? (
        <InstrumentDetailScreen route={route} navigate={setRoute} />
      ) : null}
      {route.name === "alertEvents" ? <AlertEventsScreen navigate={setRoute} /> : null}
      {route.name === "alertRules" ? <AlertRulesScreen navigate={setRoute} /> : null}
      {route.name === "settings" ? <SettingsScreen navigate={setRoute} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  }
});
```

- [ ] **Step 4: 运行 typecheck**

Run:

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: exit code 0.

- [ ] **Step 5: Commit**

```bash
git add apps/market-mobile/src/app/AppRoot.tsx apps/market-mobile/src/screens apps/market-mobile/src/components
git commit -m "Add native mobile screen placeholders"
```

## Task 3: 增加移动端 API client 和类型

**Files:**
- Create: `apps/market-mobile/src/api/client.ts`
- Create: `apps/market-mobile/src/api/types.ts`
- Create: `apps/market-mobile/src/api/market.ts`
- Create: `apps/market-mobile/src/api/alerts.ts`

- [ ] **Step 1: 创建 ApiError 和 fetchJson**

Create `apps/market-mobile/src/api/client.ts`:

```ts
import { API_BASE_URL } from "../config";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = "API_ERROR"
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const message = typeof payload.error === "string" ? payload.error : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return response.json() as Promise<T>;
}
```

- [ ] **Step 2: 定义移动端类型**

Create `apps/market-mobile/src/api/types.ts` with explicit interfaces for:

```ts
export type MobileBoardItem = {
  market: string;
  symbol: string;
  name: string;
  last_price: number | null;
  change_pct: number | null;
  turnover: number | null;
  volume: number | null;
  rank: number | null;
  rank_change: number | null;
  data_time: string | null;
};

export type MobileBoard = {
  key: string;
  title: string;
  market: string;
  data_time: string | null;
  items: MobileBoardItem[];
};

export type MobileHomePayload = {
  server_time: string;
  boards: MobileBoard[];
};

export type MobileBar = {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  turnover: number | null;
};

export type MobileInstrumentDetailPayload = {
  instrument: {
    market: string;
    symbol: string;
    name: string;
    asset_class: string | null;
  };
  snapshot: {
    last_price: number | null;
    change_pct: number | null;
    volume: number | null;
    turnover: number | null;
    data_time: string | null;
    source: string | null;
  };
  periods: string[];
  bars: MobileBar[];
};
```

- [ ] **Step 3: 创建行情 API wrapper**

Create `apps/market-mobile/src/api/market.ts`:

```ts
import { fetchJson } from "./client";
import type { MobileHomePayload, MobileInstrumentDetailPayload } from "./types";

export function fetchMobileHome(): Promise<MobileHomePayload> {
  return fetchJson<MobileHomePayload>("/api/mobile/home");
}

export function fetchMobileInstrumentDetail({
  market,
  symbol,
  period = "1d"
}: {
  market: string;
  symbol: string;
  period?: string;
}): Promise<MobileInstrumentDetailPayload> {
  const params = new URLSearchParams({ market, symbol, period });
  return fetchJson<MobileInstrumentDetailPayload>(`/api/mobile/instrument-detail?${params}`);
}
```

- [ ] **Step 4: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/api
git commit -m "Add native mobile API client"
```

## Task 4: 后端增加移动端聚合接口

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: 写失败测试**

Add tests to `tests/test_api.py`:

```python
def test_get_mobile_home_payload_returns_boards(self):
    payload = api.get_mobile_home_payload(self.connector)
    self.assertIn("server_time", payload)
    self.assertIn("boards", payload)
    self.assertIsInstance(payload["boards"], list)

def test_get_mobile_instrument_detail_payload_returns_snapshot_and_bars(self):
    payload = api.get_mobile_instrument_detail_payload(
        self.connector,
        market="HK",
        symbol="09988",
        period="1d",
    )
    self.assertIn("instrument", payload)
    self.assertIn("snapshot", payload)
    self.assertIn("periods", payload)
    self.assertIn("bars", payload)
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_mobile_home_payload_returns_boards tests.test_api.ApiTests.test_get_mobile_instrument_detail_payload_returns_snapshot_and_bars -v
```

Expected: fail because functions do not exist.

- [ ] **Step 3: 实现 payload helpers**

In `src/market/api.py`, add:

```python
def get_mobile_home_payload(connector: DatabaseConnector) -> dict[str, object]:
    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "boards": [
            get_board_payload(connector, board_key="HK_TURNOVER_TOP50"),
            get_board_payload(connector, board_key="US_TURNOVER_TOP50"),
        ],
    }

def get_mobile_instrument_detail_payload(
    connector: DatabaseConnector,
    *,
    market: str,
    symbol: str,
    period: str = "1d",
) -> dict[str, object]:
    return get_instrument_detail_payload(
        connector,
        market=market,
        symbol=symbol,
        period=period,
        daily_limit=180,
    )
```

Adjust field shape to match `MobileHomePayload` and `MobileInstrumentDetailPayload`; do not expose extra web-only keys.

- [ ] **Step 4: Add HTTP routes**

Route:

```text
GET /api/mobile/home
GET /api/mobile/instrument-detail
```

Expected behavior:

- `market` and `symbol` are required for detail.
- `period` defaults to `1d`.
- errors return JSON with non-2xx status.

- [ ] **Step 5: 运行测试并提交**

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_mobile_home_payload_returns_boards tests.test_api.ApiTests.test_get_mobile_instrument_detail_payload_returns_snapshot_and_bars -v
.venv/bin/python -m unittest discover -s tests -v
git add src/market/api.py tests/test_api.py
git commit -m "Add mobile market aggregate APIs"
```

## Task 5: 增加 60 秒 TTL 缓存

**Files:**
- Create: `apps/market-mobile/src/cache/queryCache.ts`
- Modify: `apps/market-mobile/src/screens/HomeScreen.tsx`
- Modify: `apps/market-mobile/src/screens/InstrumentDetailScreen.tsx`

- [ ] **Step 1: 创建 TTL cache**

Create:

```ts
type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const cache = new Map<string, CacheEntry<unknown>>();

export async function getCached<T>({
  key,
  ttlMs,
  load,
  force = false
}: {
  key: string;
  ttlMs: number;
  load: () => Promise<T>;
  force?: boolean;
}): Promise<T> {
  const now = Date.now();
  const existing = cache.get(key) as CacheEntry<T> | undefined;
  if (!force && existing && existing.expiresAt > now) {
    return existing.value;
  }
  const value = await load();
  cache.set(key, { value, expiresAt: now + ttlMs });
  return value;
}
```

- [ ] **Step 2: 首页使用缓存**

In `HomeScreen.tsx`, use:

```ts
getCached({
  key: "mobile-home",
  ttlMs: 60_000,
  load: fetchMobileHome,
  force
});
```

- [ ] **Step 3: 详情页使用缓存**

In `InstrumentDetailScreen.tsx`, use key:

```ts
`instrument:${route.market}:${route.symbol}:${period}`
```

- [ ] **Step 4: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/cache apps/market-mobile/src/screens
git commit -m "Add native mobile request cache"
```

## Task 6: 实现原生首页/排行榜

**Files:**
- Modify: `apps/market-mobile/src/screens/HomeScreen.tsx`
- Create: `apps/market-mobile/src/components/MarketList.tsx`
- Create: `apps/market-mobile/src/components/MarketListItem.tsx`
- Create: `apps/market-mobile/src/components/PriceChange.tsx`
- Create: `apps/market-mobile/src/components/DataTimeBadge.tsx`

- [ ] **Step 1: 实现价格和时间组件**

`PriceChange` receives `value: number | null` and renders red for positive, green for negative, gray for null.

`DataTimeBadge` receives `value: string | null` and renders `数据时间 --` when null.

- [ ] **Step 2: 实现 MarketListItem**

Each item shows:

- symbol/name
- last price
- change pct
- turnover or volume
- rank change
- data time

On press:

```ts
navigate({ name: "instrument", market: item.market, symbol: item.symbol });
```

- [ ] **Step 3: 实现 HomeScreen data flow**

HomeScreen states:

```ts
const [payload, setPayload] = useState<MobileHomePayload | null>(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState<string | null>(null);
```

Use `fetchMobileHome` through cache on mount and pull-to-refresh with force.

- [ ] **Step 4: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/screens/HomeScreen.tsx apps/market-mobile/src/components
git commit -m "Build native mobile home screen"
```

## Task 7: 实现原生详情页和数据时间展示

**Files:**
- Modify: `apps/market-mobile/src/screens/InstrumentDetailScreen.tsx`
- Create: `apps/market-mobile/src/components/NativeKLineChart.tsx`

- [ ] **Step 1: 实现详情页头部**

Display:

- market/symbol/name
- last price
- change pct
- volume/turnover
- source
- data time

- [ ] **Step 2: 实现简化 K 线组件**

`NativeKLineChart` props:

```ts
import type { MobileBar } from "../api/types";

export function NativeKLineChart({ bars }: { bars: MobileBar[] }) {
  // First version renders fixed-width candle rows with React Native View.
}
```

First version requirements:

- no WebView
- no CDN
- no external chart dependency
- handles empty bars with `EmptyState`
- renders at stable height

- [ ] **Step 3: 周期切换**

Add period tabs from `payload.periods`. Pressing a period refetches detail with the selected period and TTL cache.

- [ ] **Step 4: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/screens/InstrumentDetailScreen.tsx apps/market-mobile/src/components/NativeKLineChart.tsx
git commit -m "Build native instrument detail screen"
```

## Task 8: 通知点击改成原生路由

**Files:**
- Create: `apps/market-mobile/src/alerts/notificationRouter.ts`
- Modify: `apps/market-mobile/src/app/AppRoot.tsx`

- [ ] **Step 1: 创建 notification router**

Create:

```ts
import type { AppRoute } from "../app/navigation";

export function routeFromNotificationData(data: Record<string, unknown>): AppRoute {
  const market = typeof data.market === "string" ? data.market : null;
  const symbol = typeof data.symbol === "string" ? data.symbol : null;
  if (market && symbol) {
    return { name: "instrument", market, symbol };
  }
  return { name: "alertEvents" };
}
```

- [ ] **Step 2: AppRoot 监听通知点击**

Move existing `Notifications.getLastNotificationResponse()` and
`addNotificationResponseReceivedListener` logic from old `App.tsx` into `AppRoot.tsx`.

On notification open:

```ts
setRoute(routeFromNotificationData(data));
```

- [ ] **Step 3: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/alerts/notificationRouter.ts apps/market-mobile/src/app/AppRoot.tsx
git commit -m "Route push notifications to native screens"
```

## Task 9: 接回推送注册、SSE 和回前台补拉

**Files:**
- Modify: `apps/market-mobile/src/app/AppRoot.tsx`
- Modify: `apps/market-mobile/src/onlineAlerts.ts`
- Modify: `apps/market-mobile/src/alertEvents.ts`

- [ ] **Step 1: 把现有注册流程迁入 AppRoot**

Keep current sequence:

1. `initializeGetuiPush()`
2. `getExpoPushToken()`
3. `waitForGetuiClientId()`
4. `registerDeviceForPush({ pushToken, getuiCid })`
5. `pullMissedAlertEvents()`

- [ ] **Step 2: active 时补拉**

Use `AppState.addEventListener("change", ...)` and call pull when state becomes `active`.

- [ ] **Step 3: SSE 只在前台连接**

Keep current `startOnlineAlerts` behavior. Ensure it receives:

```ts
getPushToken
getAfterId
setAfterId
seenEventIds
```

- [ ] **Step 4: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/app/AppRoot.tsx apps/market-mobile/src/onlineAlerts.ts apps/market-mobile/src/alertEvents.ts
git commit -m "Wire native app alert runtime"
```

## Task 10: 实现提醒事件和规则页面

**Files:**
- Modify: `apps/market-mobile/src/screens/AlertEventsScreen.tsx`
- Modify: `apps/market-mobile/src/screens/AlertRulesScreen.tsx`
- Modify: `apps/market-mobile/src/api/alerts.ts`
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: 后端确认移动端提醒接口**

Ensure these routes exist:

```text
GET /api/mobile/alert-events
POST /api/mobile/alert-events/ack
GET /api/mobile/alert-rules
POST /api/mobile/alert-rules
PATCH /api/mobile/alert-rules/{id}
```

- [ ] **Step 2: API wrapper**

`apps/market-mobile/src/api/alerts.ts` exports:

```ts
export function fetchMobileAlertEvents(...)
export function acknowledgeMobileAlertEvents(...)
export function fetchMobileAlertRules(...)
export function createMobileAlertRule(...)
export function patchMobileAlertRule(...)
```

- [ ] **Step 3: AlertEventsScreen**

Render recent events, navigate to instrument detail on press.

- [ ] **Step 4: AlertRulesScreen**

Render rules with enable/disable toggle using `PATCH`.

- [ ] **Step 5: 测试并提交**

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_get_alert_events_payload_returns_recent_events tests.test_api.ApiTests.test_get_alert_rules_payload_returns_rules -v
cd apps/market-mobile
npm run typecheck
git add src/market/api.py tests/test_api.py apps/market-mobile/src/api/alerts.ts apps/market-mobile/src/screens
git commit -m "Build native alert management screens"
```

## Task 11: 设置页和调试状态

**Files:**
- Modify: `apps/market-mobile/src/screens/SettingsScreen.tsx`
- Modify: `apps/market-mobile/src/oneplusGuidance.ts`

- [ ] **Step 1: 展示连接信息**

Settings shows:

- API base URL
- Expo Push Token status
- Getui CID status
- SSE active/inactive
- latest seen alert event id

- [ ] **Step 2: 展示 Android 后台设置提示**

Use existing `oneplusGuidance.ts` content and render concise checklist.

- [ ] **Step 3: 运行 typecheck 并提交**

```bash
cd apps/market-mobile
npm run typecheck
git add apps/market-mobile/src/screens/SettingsScreen.tsx apps/market-mobile/src/oneplusGuidance.ts
git commit -m "Add native mobile settings diagnostics"
```

## Task 12: 删除 WebView 依赖和遗留配置

**Files:**
- Modify: `apps/market-mobile/package.json`
- Modify: `apps/market-mobile/package-lock.json`
- Modify: `apps/market-mobile/App.tsx`
- Optional delete: `apps/market-mobile/plugins/withCleartextTraffic.js`
- Optional modify: `apps/market-mobile/app.json`

- [ ] **Step 1: 确认 App 不再 import WebView**

Run:

```bash
rg "WebView|react-native-webview|WEB_BASE_URL|sourceUri" apps/market-mobile
```

Expected: no production import or usage.

- [ ] **Step 2: 卸载 WebView 依赖**

Run:

```bash
cd apps/market-mobile
npm uninstall react-native-webview
npm run typecheck
```

Expected: typecheck passes.

- [ ] **Step 3: 移除明文 WebView 配置**

If app now uses HTTPS API only, remove cleartext plugin from `app.json` and delete `plugins/withCleartextTraffic.js`.

- [ ] **Step 4: Commit**

```bash
git add apps/market-mobile/package.json apps/market-mobile/package-lock.json apps/market-mobile/app.json apps/market-mobile/plugins apps/market-mobile/App.tsx
git commit -m "Remove mobile WebView dependency"
```

## Task 13: 本地构建和真机验证

**Files:**
- Modify only if failures require fixes.

- [x] **Step 1: Typecheck**

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: exit code 0.

- [x] **Step 2: Android release build**

```bash
source /Users/dt.shi/work/android-local-build/env.sh
cd /Users/dt.shi/work/mono/apps/market-mobile/android
./gradlew assembleRelease
```

Expected: `BUILD SUCCESSFUL`.

- [x] **Step 3: Install APK**

```bash
source /Users/dt.shi/work/android-local-build/env.sh
adb install -r /Users/dt.shi/work/mono/apps/market-mobile/android/app/build/outputs/apk/release/app-release.apk
```

Expected: app installs on connected Android device.

- [ ] **Step 4: Manual verification checklist**

Verify on phone:

- [ ] app first screen is native, not webpage
- [ ] home board loads
- [ ] HK detail opens from board
- [ ] detail shows data time
- [ ] K line renders without CDN/WebView
- [ ] notification permission works
- [ ] Getui CID registers
- [ ] foreground SSE alert arrives
- [ ] background Push arrives
- [ ] notification tap opens native instrument page
- [ ] returning to foreground pulls missed alerts without duplicates

- [x] **Step 5: Commit fixes if needed**

```bash
git add <fixed-files>
git commit -m "Stabilize native mobile Android build"
```

## Task 14: 部署后端移动端 API

**Files:**
- Server deployment only.

- [x] **Step 1: Push branch**

```bash
git push origin codex/market-data-platform
```

- [x] **Step 2: Deploy on Tencent**

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && git pull --ff-only origin codex/market-data-platform && .venv/bin/python -m pip install -e . && sudo systemctl restart market-api && sleep 2 && systemctl is-active market-api'
```

Expected:

```text
active
```

- [x] **Step 3: Smoke test API**

```bash
curl -sS http://150.109.22.77:8000/api/mobile/home
curl -sS 'http://150.109.22.77:8000/api/mobile/instrument-detail?market=HK&symbol=09988&period=1d'
```

Expected: JSON payloads match mobile types.

## Self-Review Checklist

- [x] Plan explicitly removes WebView and does not keep it as fallback.
- [x] Plan keeps current push/SSE/backfill architecture.
- [x] Plan avoids adding chart dependencies in the first implementation pass.
- [x] Plan includes backend mobile aggregate APIs.
- [x] Plan includes Android true-device validation.
- [x] Plan avoids direct database access from App.
