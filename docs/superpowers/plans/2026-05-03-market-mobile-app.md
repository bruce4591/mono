# Market Mobile App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-used Android app for the existing market web product, with better mobile display and reliable alert delivery on a OnePlus 13T.

**Architecture:** Create an Expo Android app under `apps/market-mobile/` that first wraps the existing web UI in a WebView, then adds native notification permission, device push registration, and alert management. Background alerts must be judged on the Tencent server and delivered by push; the app may use local polling only while foregrounded because OnePlus/ColorOS/OxygenOS can suspend background timers.

**Tech Stack:** Expo, React Native, `react-native-webview`, `expo-notifications`, existing Python market API on `tencent-market`, SQLite alert data, Expo Push API first with pluggable backup channels.

---

## Scope

### In Scope

- Add a self-used Android app, not published to any app market.
- Open the current market web UI inside the app for the first usable version.
- Add native notification permission and push-token registration.
- Add foreground local alerts for active app sessions.
- Add server-side alert evaluation for background, locked-screen, and killed-app scenarios.
- Add OnePlus 13T setup guidance in the app or docs: notification permission, battery optimization exemption, auto-start/background run, lock-screen notifications.
- Preserve the current web product and API behavior.

### Out of Scope For MVP

- Native K-line rendering.
- App Store / Google Play release.
- Multi-user auth.
- Multi-device sync beyond registering device tokens.
- Guaranteed sub-second alerts while the phone is offline or push providers are delayed.

## Key Decision: Background Alerts

Android apps cannot reliably run 15-30 second polling after they move to the background. This is especially important on OnePlus 13T because domestic Android power management can pause timers, stop background work, or kill the app process.

Use this split:

- Foreground app: poll market API every 15-30 seconds and show local notifications.
- Background, locked screen, or killed app: server evaluates alert rules and sends push notifications.
- If Expo/FCM push is delayed in the user's network environment, add a backup notification channel such as Bark, ntfy, Telegram Bot, Enterprise WeChat bot, email, or SMS.

## File Structure

- Create: `apps/market-mobile/package.json`
  - Expo app dependencies and scripts.
- Create: `apps/market-mobile/app.json`
  - Android package name, app name, notification config, icon/splash metadata.
- Create: `apps/market-mobile/App.tsx`
  - Main app shell with WebView, native alert entry point, Android back handling.
- Create: `apps/market-mobile/src/config.ts`
  - API and web base URLs.
- Create: `apps/market-mobile/src/notifications.ts`
  - Notification permission, Expo Push Token registration, local notification helper.
- Create: `apps/market-mobile/src/foregroundAlerts.ts`
  - Foreground polling and local rule evaluation.
- Create: `apps/market-mobile/src/oneplusGuidance.ts`
  - OnePlus 13T permission checklist text used by UI/docs.
- Modify: `pyproject.toml`
  - Add CLI entry if a push registration/admin helper is needed.
- Modify: `src/market/schema.sql`
  - Add push devices, alert rules, and alert event tables if existing alert schema is insufficient.
- Modify: `src/market/api.py`
  - Add device registration and alert-rule endpoints.
- Modify: `src/market/alerts.py`
  - Add server-side rule evaluation against latest `market_snapshot`.
- Create: `src/market/push.py`
  - Expo Push API client with retry-safe responses.
- Create: `tests/test_push.py`
  - Unit tests for push payload and failure handling.
- Modify: `tests/test_api.py`
  - API tests for push token registration and alert rule CRUD.
- Modify: `tests/test_alerts.py`
  - Rule evaluation and cooldown tests.
- Modify: `README.md`
  - Android app setup, APK build, OnePlus 13T background notification checklist.

---

## Task 1: Create Expo Android App Shell

**Files:**
- Create: `apps/market-mobile/package.json`
- Create: `apps/market-mobile/app.json`
- Create: `apps/market-mobile/App.tsx`
- Create: `apps/market-mobile/src/config.ts`

- [ ] **Step 1: Create the Expo app package**

Create `apps/market-mobile/package.json`:

```json
{
  "name": "market-mobile",
  "version": "0.1.0",
  "private": true,
  "main": "node_modules/expo/AppEntry.js",
  "scripts": {
    "start": "expo start",
    "android": "expo run:android",
    "build:android": "eas build -p android --profile preview"
  },
  "dependencies": {
    "expo": "~53.0.0",
    "expo-notifications": "~0.31.0",
    "react": "19.0.0",
    "react-native": "0.79.0",
    "react-native-webview": "^13.12.5"
  },
  "devDependencies": {
    "@types/react": "~19.0.10",
    "typescript": "~5.8.3"
  }
}
```

- [ ] **Step 2: Create Android app config**

Create `apps/market-mobile/app.json`:

```json
{
  "expo": {
    "name": "Market",
    "slug": "market-mobile",
    "version": "0.1.0",
    "orientation": "portrait",
    "userInterfaceStyle": "dark",
    "android": {
      "package": "com.local.marketmobile",
      "permissions": ["POST_NOTIFICATIONS"]
    },
    "plugins": ["expo-notifications"]
  }
}
```

- [ ] **Step 3: Add base URL config**

Create `apps/market-mobile/src/config.ts`:

```ts
export const WEB_BASE_URL = "https://market.dts.local";
export const API_BASE_URL = "https://market.dts.local";
export const FOREGROUND_POLL_MS = 15000;
```

Before building the APK, set both constants to the public HTTPS origin served by `tencent-market`; the app must not be built with a private LAN or localhost URL.

- [ ] **Step 4: Add WebView shell**

Create `apps/market-mobile/App.tsx`:

```tsx
import React, { useEffect, useRef, useState } from "react";
import { BackHandler, SafeAreaView, StatusBar, StyleSheet } from "react-native";
import { WebView } from "react-native-webview";
import type { WebView as WebViewType } from "react-native-webview";
import { WEB_BASE_URL } from "./src/config";

export default function App() {
  const webViewRef = useRef<WebViewType>(null);
  const [canGoBack, setCanGoBack] = useState(false);

  useEffect(() => {
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack) {
        webViewRef.current?.goBack();
        return true;
      }
      return false;
    });

    return () => subscription.remove();
  }, [canGoBack]);

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      <WebView
        ref={webViewRef}
        source={{ uri: WEB_BASE_URL }}
        onNavigationStateChange={(state) => setCanGoBack(state.canGoBack)}
        sharedCookiesEnabled
        allowsBackForwardNavigationGestures
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  }
});
```

- [ ] **Step 5: Verify local app start**

Run:

```bash
cd apps/market-mobile
npm install
npx expo start
```

Expected: Expo starts without TypeScript or dependency errors.

- [ ] **Step 6: Commit**

```bash
git add apps/market-mobile
git commit -m "Add market mobile app shell"
```

---

## Task 2: Add Native Notification Permission And Push Token Registration

**Files:**
- Create: `apps/market-mobile/src/notifications.ts`
- Modify: `apps/market-mobile/App.tsx`
- Modify: `src/market/schema.sql`
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add mobile notification helper**

Create `apps/market-mobile/src/notifications.ts`:

```ts
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";
import { API_BASE_URL } from "./config";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowAlert: true,
    shouldPlaySound: true,
    shouldSetBadge: false
  })
});

export async function registerDeviceForPush(): Promise<string | null> {
  const current = await Notifications.getPermissionsAsync();
  const permission =
    current.status === "granted" ? current : await Notifications.requestPermissionsAsync();

  if (permission.status !== "granted") {
    return null;
  }

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("market-alerts", {
      name: "Market Alerts",
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: "#22c55e"
    });
  }

  const token = await Notifications.getExpoPushTokenAsync();
  await fetch(`${API_BASE_URL}/api/mobile/devices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform: Platform.OS,
      push_token: token.data,
      device_label: "OnePlus 13T"
    })
  });

  return token.data;
}

export async function showLocalAlert(title: string, body: string): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: { title, body, sound: true },
    trigger: null
  });
}
```

- [ ] **Step 2: Call push registration on app start**

Modify `apps/market-mobile/App.tsx` to import and call registration:

```tsx
import { registerDeviceForPush } from "./src/notifications";
```

Inside `App()` add:

```tsx
useEffect(() => {
  registerDeviceForPush().catch(() => undefined);
}, []);
```

- [ ] **Step 3: Add device table**

Modify `src/market/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS push_device (
    push_device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_token TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    device_label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
```

- [ ] **Step 4: Add API endpoint test**

Add to `tests/test_api.py`:

```python
def test_register_mobile_device_upserts_push_token(self):
    response = self.client.post(
        "/api/mobile/devices",
        json={
            "platform": "android",
            "push_token": "ExponentPushToken[test-token]",
            "device_label": "OnePlus 13T",
        },
    )

    self.assertEqual(response.status_code, 200)
    rows = self.connection.execute(
        "SELECT platform, push_token, device_label, enabled FROM push_device"
    ).fetchall()
    self.assertEqual(len(rows), 1)
    self.assertEqual(rows[0]["platform"], "android")
    self.assertEqual(rows[0]["push_token"], "ExponentPushToken[test-token]")
    self.assertEqual(rows[0]["device_label"], "OnePlus 13T")
    self.assertEqual(rows[0]["enabled"], 1)
```

- [ ] **Step 5: Run test to verify it fails before API implementation**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_register_mobile_device_upserts_push_token -v
```

Expected: FAIL because `/api/mobile/devices` does not exist.

- [ ] **Step 6: Implement endpoint**

Add a FastAPI handler in `src/market/api.py` that validates `platform`, `push_token`, and optional `device_label`, then upserts into `push_device`.

- [ ] **Step 7: Run test to verify it passes**

Run:

```bash
.venv/bin/python -m unittest tests.test_api.ApiTests.test_register_mobile_device_upserts_push_token -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add apps/market-mobile/src/notifications.ts apps/market-mobile/App.tsx src/market/schema.sql src/market/api.py tests/test_api.py
git commit -m "Register mobile push devices"
```

---

## Task 3: Add OnePlus 13T Background Notification Guidance

**Files:**
- Create: `apps/market-mobile/src/oneplusGuidance.ts`
- Modify: `README.md`

- [ ] **Step 1: Create OnePlus checklist copy**

Create `apps/market-mobile/src/oneplusGuidance.ts`:

```ts
export const ONEPLUS_13T_BACKGROUND_CHECKLIST = [
  "允许通知权限",
  "允许锁屏通知",
  "电池设置改为不优化或允许后台运行",
  "允许自启动或后台启动",
  "如果系统提示高耗电，把 Market App 加入白名单"
];
```

- [ ] **Step 2: Document device setup**

Add to `README.md`:

```md
### Android self-use app and OnePlus 13T alerts

The Android app uses native notifications. On OnePlus 13T, enable:

- Notification permission.
- Lock-screen notifications.
- Battery setting: no optimization or allow background running.
- Auto-start/background launch permission.
- High power usage whitelist if the system prompts.

Foreground alerts can poll the market API every 15-30 seconds. Background,
locked-screen, or killed-app alerts must be evaluated on `tencent-market` and
delivered by push notification because Android may suspend app timers.
```

- [ ] **Step 3: Commit**

```bash
git add apps/market-mobile/src/oneplusGuidance.ts README.md
git commit -m "Document OnePlus alert permissions"
```

---

## Task 4: Add Server-Side Alert Rules For Background Push

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/alerts.py`
- Modify: `src/market/api.py`
- Modify: `tests/test_alerts.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Add alert storage tables**

Modify `src/market/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS mobile_alert_rule (
    mobile_alert_rule_id INTEGER PRIMARY KEY AUTOINCREMENT,
    push_device_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    condition_type TEXT NOT NULL,
    threshold REAL NOT NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 900,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);

CREATE TABLE IF NOT EXISTS mobile_alert_event (
    mobile_alert_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile_alert_rule_id INTEGER NOT NULL,
    triggered_at_utc TEXT NOT NULL,
    observed_value REAL NOT NULL,
    message TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    FOREIGN KEY (mobile_alert_rule_id) REFERENCES mobile_alert_rule(mobile_alert_rule_id)
);
```

Use `condition_type` values:

- `price_above`
- `price_below`
- `change_pct_above`
- `change_pct_below`

- [ ] **Step 2: Add rule evaluation tests**

Add tests to `tests/test_alerts.py` that assert:

- `price_above` fires when `last_price > threshold`.
- `price_below` fires when `last_price < threshold`.
- A rule inside cooldown does not fire twice.
- Disabled rules do not fire.

- [ ] **Step 3: Implement evaluator**

Add functions in `src/market/alerts.py`:

```python
def evaluate_mobile_alert_rules(connection: sqlite3.Connection, now_utc: str) -> list[dict[str, object]]:
    ...
```

The evaluator must read latest `market_snapshot` rows, compare enabled rules, and return pending push messages while inserting `mobile_alert_event` rows for fired rules.

- [ ] **Step 4: Add API tests for rule creation**

Add tests to `tests/test_api.py` for:

- `POST /api/mobile/alert-rules`
- `GET /api/mobile/alert-rules?push_token=...`
- disabling a rule

- [ ] **Step 5: Implement alert rule endpoints**

Add endpoints in `src/market/api.py`:

- `POST /api/mobile/alert-rules`
- `GET /api/mobile/alert-rules`
- `PATCH /api/mobile/alert-rules/{rule_id}`

- [ ] **Step 6: Run alert/API tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_alerts tests.test_api -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/market/schema.sql src/market/alerts.py src/market/api.py tests/test_alerts.py tests/test_api.py
git commit -m "Add mobile alert rules"
```

---

## Task 5: Add Push Delivery Worker

**Files:**
- Create: `src/market/push.py`
- Modify: `src/market/cli.py`
- Create: `tests/test_push.py`

- [ ] **Step 1: Add push client tests**

Create `tests/test_push.py` with tests for:

- Expo Push payload contains `to`, `title`, `body`, and `sound`.
- Invalid or disabled tokens are skipped.
- HTTP errors return a failed delivery result instead of crashing the worker.

- [ ] **Step 2: Implement Expo Push client**

Create `src/market/push.py`:

```python
EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"

def build_expo_push_payload(token: str, title: str, body: str) -> dict[str, object]:
    return {
        "to": token,
        "title": title,
        "body": body,
        "sound": "default",
        "channelId": "market-alerts",
    }
```

Add a sender function that posts JSON to Expo and returns structured success/failure data.

- [ ] **Step 3: Add CLI worker command**

Modify `src/market/cli.py` to add:

```bash
market evaluate-mobile-alerts
```

The command must:

1. Open the configured database.
2. Run `evaluate_mobile_alert_rules`.
3. Send push messages.
4. Write delivery result into `mobile_alert_event.delivery_status`.

- [ ] **Step 4: Run push tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_push tests.test_alerts -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/market/push.py src/market/cli.py tests/test_push.py
git commit -m "Send mobile alert push notifications"
```

---

## Task 6: Add Foreground Local Alerts In The App

**Files:**
- Create: `apps/market-mobile/src/foregroundAlerts.ts`
- Modify: `apps/market-mobile/App.tsx`

- [ ] **Step 1: Add foreground polling module**

Create `apps/market-mobile/src/foregroundAlerts.ts`:

```ts
import { API_BASE_URL, FOREGROUND_POLL_MS } from "./config";
import { showLocalAlert } from "./notifications";

type AlertRule = {
  symbol: string;
  market: string;
  condition_type: "price_above" | "price_below" | "change_pct_above" | "change_pct_below";
  threshold: number;
};

export function startForegroundAlerts(getRules: () => AlertRule[]): () => void {
  let stopped = false;
  const lastTriggered = new Map<string, number>();

  async function tick() {
    if (stopped) {
      return;
    }

    const rules = getRules();
    for (const rule of rules) {
      const response = await fetch(`${API_BASE_URL}/api/instruments/${rule.market}/${rule.symbol}/snapshot`);
      if (!response.ok) {
        continue;
      }
      const snapshot = await response.json();
      const value = rule.condition_type.startsWith("price")
        ? Number(snapshot.last_price)
        : Number(snapshot.change_pct);
      const fired =
        (rule.condition_type.endsWith("above") && value > rule.threshold) ||
        (rule.condition_type.endsWith("below") && value < rule.threshold);
      const key = `${rule.market}:${rule.symbol}:${rule.condition_type}:${rule.threshold}`;
      const now = Date.now();
      if (fired && now - (lastTriggered.get(key) ?? 0) > 900000) {
        lastTriggered.set(key, now);
        await showLocalAlert(`${rule.symbol} 价格提醒`, `当前值 ${value}, 阈值 ${rule.threshold}`);
      }
    }

    setTimeout(tick, FOREGROUND_POLL_MS);
  }

  tick().catch(() => undefined);

  return () => {
    stopped = true;
  };
}
```

- [ ] **Step 2: Start foreground polling from the app**

Modify `apps/market-mobile/App.tsx` to start foreground alerts after notification registration. The initial rule source can be an empty list until the native rule UI is added:

```tsx
useEffect(() => {
  const stop = startForegroundAlerts(() => []);
  return stop;
}, []);
```

- [ ] **Step 3: Run TypeScript check**

Run:

```bash
cd apps/market-mobile
npx tsc --noEmit
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add apps/market-mobile/src/foregroundAlerts.ts apps/market-mobile/App.tsx
git commit -m "Add foreground mobile alerts"
```

---

## Task 7: Deploy Server Alert Worker

**Files:**
- Modify: `README.md`
- Modify or create deployment script under existing deployment conventions.

- [ ] **Step 1: Add server worker command docs**

Add to `README.md`:

```md
### Mobile alert worker

Run the server-side alert evaluator on `tencent-market` so Android background,
locked-screen, and killed-app reminders do not depend on local app polling.

Recommended MVP schedule:

```cron
* * * * * cd /home/ubuntu/github/mono && .venv/bin/market evaluate-mobile-alerts >> logs/mobile-alerts.log 2>&1
```

Crypto/futures prices are already updated by WebSocket snapshots, so the worker
only needs to evaluate latest snapshots and send push messages. Keep cooldowns
in the database to avoid repeated notifications.
```

- [ ] **Step 2: Deploy to Tencent**

Run:

```bash
git push origin codex-market-mvp
ssh tencent-market 'cd /home/ubuntu/github/mono && git pull --ff-only && .venv/bin/python -m unittest discover -s tests -v'
```

Expected: server fast-forwards and tests pass.

- [ ] **Step 3: Install cron or systemd timer**

On `tencent-market`, install a minute-level cron for `market evaluate-mobile-alerts`.

Expected: `logs/mobile-alerts.log` shows rule evaluation without crashes.

- [ ] **Step 4: Commit deployment docs**

```bash
git add README.md
git commit -m "Document mobile alert worker deployment"
```

---

## Task 8: Build Self-Use Android APK

**Files:**
- Create: `apps/market-mobile/eas.json`
- Modify: `README.md`

- [ ] **Step 1: Add EAS preview build profile**

Create `apps/market-mobile/eas.json`:

```json
{
  "cli": {
    "version": ">= 16.0.0"
  },
  "build": {
    "preview": {
      "android": {
        "buildType": "apk"
      }
    }
  }
}
```

- [ ] **Step 2: Build APK**

Run:

```bash
cd apps/market-mobile
npx eas-cli@latest build -p android --profile preview
```

Expected: EAS returns an APK download link.

- [ ] **Step 3: Install on OnePlus 13T**

Transfer the APK to the phone and install it manually. Then enable the OnePlus checklist from Task 3.

- [ ] **Step 4: Smoke test**

Expected:

- App opens the market web UI.
- Android back button navigates inside WebView.
- Notification permission prompt appears.
- Device token appears in the server `push_device` table.
- A test alert produces a notification while the screen is locked.

- [ ] **Step 5: Commit**

```bash
git add apps/market-mobile/eas.json README.md
git commit -m "Add Android APK build profile"
```

---

## Backup Notification Channels

If push notification latency is unacceptable on OnePlus 13T or the current network, add one of these as a second delivery target while keeping the same server-side rule evaluator:

- Bark: simple iOS-style webhook server, can be self-hosted or app-based.
- ntfy: simple HTTP topic notifications, easy to self-host.
- Telegram Bot: reliable if Telegram is reachable.
- Enterprise WeChat bot: practical in China network environments.
- SMS/email: slower and noisier, but useful for critical rules.

The server data model should keep delivery channel separate from rule evaluation so these can be added without changing alert logic.

## Validation Checklist

- `.venv/bin/python -m unittest discover -s tests -v` passes.
- `cd apps/market-mobile && npx tsc --noEmit` passes.
- App launches on Android.
- OnePlus 13T notification permission is granted.
- Device token registration appears in `push_device`.
- Foreground local notification works.
- Server-side push works while the app is backgrounded or screen is locked.
- Cooldown prevents repeated notifications.

## Self-Review

- No native K-line rewrite is required for MVP.
- Background reminders are not implemented as app-side timers because Android can suspend them.
- OnePlus 13T device-specific setup is explicitly documented.
- Server-side push is the reliable path for background, locked-screen, and killed-app scenarios.
- Backup channels are isolated as future delivery adapters, not mixed into rule evaluation.
