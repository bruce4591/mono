# Alerting Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current mobile push proof of concept into a reliable alerting platform with custom indicators, online delivery, system push fallback, message replay, and Agent-controlled APIs.

**Architecture:** Treat alert events as the only source of truth. Rule evaluation creates durable `mobile_alert_event` rows, delivery workers create per-device `mobile_alert_delivery` records, online streams and system push become interchangeable delivery channels, and the mobile app always reconciles by pulling missed events from the server.

**Tech Stack:** Python stdlib API server, SQLite, existing `market` package, Expo React Native Android app, Getui Android push, future SSE online stream.

---

## Current Baseline

- Mobile app package: `com.local.marketmobile`.
- Mobile device registration exists in `src/market/api.py::register_mobile_device`.
- Mobile rules exist in `mobile_alert_rule`.
- Mobile events exist in `mobile_alert_event`, currently carrying `delivery_status` directly.
- Push delivery exists in `src/market/push.py::deliver_mobile_alert_pushes`.
- Getui system push channel is working after adding Android offline `push_channel.android.ups.notification`.

The next work must preserve the working Getui channel while moving toward an outbox model.

---

## Target Flow

```text
market data / Agent / manual input
  -> indicator_value
  -> mobile_alert_rule evaluation
  -> mobile_alert_event
  -> mobile_alert_delivery
  -> online SSE if app is connected
  -> Getui system push if app is offline or online ACK times out
  -> app launch/resume pulls missed mobile_alert_event rows
```

---

## File Structure

**Server schema and migrations**
- Modify: `src/market/schema.sql`
- Modify: `src/market/db.py`

**Alert evaluation and custom indicators**
- Modify: `src/market/alerts.py`
- Create: `src/market/indicators.py`
- Create: `tests/test_indicators.py`
- Modify: `tests/test_alerts.py`

**Delivery outbox**
- Modify: `src/market/push.py`
- Create: `src/market/mobile_delivery.py`
- Create: `tests/test_mobile_delivery.py`
- Modify: `tests/test_push.py`

**API layer**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

**CLI and worker**
- Modify: `src/market/cli.py`
- Modify: `tests/test_cli.py`

**Mobile app**
- Modify: `apps/market-mobile/App.tsx`
- Modify: `apps/market-mobile/src/notifications.ts`
- Create: `apps/market-mobile/src/alertEvents.ts`
- Create: `apps/market-mobile/src/onlineAlerts.ts`

**Docs**
- Modify: `docs/2026-05-03-market-mobile-app.md` if present; otherwise add implementation notes to this plan after each completed phase.

---

## Phase 1: Durable Event and Delivery Outbox

### Task 1: Add Delivery Tables

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/db.py`
- Test: `tests/test_alerts.py`

- [ ] Add `mobile_alert_delivery`:

```sql
CREATE TABLE IF NOT EXISTS mobile_alert_delivery (
    mobile_alert_delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mobile_alert_event_id INTEGER NOT NULL,
    push_device_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    provider_message_id TEXT,
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (mobile_alert_event_id) REFERENCES mobile_alert_event(mobile_alert_event_id),
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id),
    UNIQUE (mobile_alert_event_id, push_device_id, channel)
);
```

- [ ] Add `device_checkpoint`:

```sql
CREATE TABLE IF NOT EXISTS device_checkpoint (
    push_device_id INTEGER PRIMARY KEY,
    last_seen_mobile_alert_event_id INTEGER NOT NULL DEFAULT 0,
    last_ack_mobile_alert_event_id INTEGER NOT NULL DEFAULT 0,
    updated_at_utc TEXT NOT NULL,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);
```

- [ ] Add `device_session`:

```sql
CREATE TABLE IF NOT EXISTS device_session (
    session_id TEXT PRIMARY KEY,
    push_device_id INTEGER NOT NULL,
    transport TEXT NOT NULL,
    connected_at_utc TEXT NOT NULL,
    last_seen_at_utc TEXT NOT NULL,
    disconnected_at_utc TEXT,
    FOREIGN KEY (push_device_id) REFERENCES push_device(push_device_id)
);
```

- [ ] Add migration helpers in `src/market/db.py` using `ALTER TABLE` only where existing tables need new columns. New tables can be created by `schema.sql`.

- [ ] Add schema test in `tests/test_alerts.py`:

```python
def test_mobile_delivery_tables_exist(self):
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "market.sqlite3"
        init_database(db_path)
        with connect(db_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
    self.assertIn("mobile_alert_delivery", tables)
    self.assertIn("device_checkpoint", tables)
    self.assertIn("device_session", tables)
```

- [ ] Run:

```bash
.venv/bin/python -m unittest tests.test_alerts -v
```

Expected: PASS.

- [ ] Commit:

```bash
git add src/market/schema.sql src/market/db.py tests/test_alerts.py
git commit -m "Add mobile alert delivery tables"
```

### Task 2: Move Delivery Status Into Outbox

**Files:**
- Create: `src/market/mobile_delivery.py`
- Modify: `src/market/push.py`
- Modify: `tests/test_push.py`
- Test: `tests/test_mobile_delivery.py`

- [ ] Create `src/market/mobile_delivery.py` with functions:

```python
def enqueue_mobile_alert_delivery(
    connection: sqlite3.Connection,
    *,
    event_id: int,
    push_device_id: int,
    channel: str,
    now_utc: str,
) -> int:
    ...

def mark_mobile_alert_delivery(
    connection: sqlite3.Connection,
    *,
    delivery_id: int,
    status: str,
    provider_message_id: str | None,
    error: str | None,
    now_utc: str,
) -> None:
    ...

def list_pending_mobile_alert_deliveries(
    connection: sqlite3.Connection,
    *,
    limit: int = 100,
) -> list[dict[str, object]]:
    ...
```

- [ ] `enqueue_mobile_alert_delivery` must use `ON CONFLICT(mobile_alert_event_id, push_device_id, channel) DO UPDATE SET updated_at_utc = excluded.updated_at_utc`.

- [ ] Update `deliver_mobile_alert_pushes` so rule evaluation creates events, then each event creates or updates a `mobile_alert_delivery` row. Keep writing `mobile_alert_event.delivery_status` during transition for backward compatibility.

- [ ] Add test:

```python
def test_deliver_mobile_alert_pushes_writes_delivery_outbox(self):
    ...
    self.assertEqual(delivery["channel"], "getui")
    self.assertEqual(delivery["status"], "sent")
```

- [ ] Run:

```bash
.venv/bin/python -m unittest tests.test_push tests.test_mobile_delivery -v
```

Expected: PASS.

- [ ] Commit:

```bash
git add src/market/mobile_delivery.py src/market/push.py tests/test_push.py tests/test_mobile_delivery.py
git commit -m "Track mobile alert delivery attempts"
```

---

## Phase 2: App Reconciliation and Missed Message Pull

### Task 3: Add Pull API for Mobile Alert Events

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] Add endpoint:

```text
GET /api/mobile/alert-events?push_token=<token>&after_id=<event_id>&limit=100
```

Response:

```json
{
  "events": [
    {
      "mobile_alert_event_id": 1,
      "market": "CRYPTO",
      "symbol": "BTCUSDT",
      "title": "BTCUSDT 价格提醒",
      "body": "BTCUSDT last_price 69000 > 68000",
      "triggered_at_utc": "2026-05-05T00:00:00Z",
      "delivery_status": "sent",
      "data": {
        "url": "/instrument.html?market=CRYPTO&symbol=BTCUSDT"
      }
    }
  ]
}
```

- [ ] The query must join `mobile_alert_event -> mobile_alert_rule -> push_device` and filter by `push_token`.

- [ ] Add test:

```python
def test_get_mobile_alert_events_after_id_filters_by_device(self):
    ...
    self.assertEqual(payload["events"][0]["mobile_alert_event_id"], expected_id)
```

- [ ] Run:

```bash
.venv/bin/python -m unittest tests.test_api -v
```

Expected: PASS.

- [ ] Commit:

```bash
git add src/market/api.py tests/test_api.py
git commit -m "Add mobile alert event pull API"
```

### Task 4: Add App Pull-on-Resume

**Files:**
- Create: `apps/market-mobile/src/alertEvents.ts`
- Modify: `apps/market-mobile/App.tsx`
- Modify: `apps/market-mobile/src/notifications.ts`

- [ ] Create `fetchMobileAlertEvents({ pushToken, afterId })`.

- [ ] Store latest seen event id in React state first. Do not add persistent storage until the server flow is verified.

- [ ] On app startup and foreground resume, call the pull API.

- [ ] Deduplicate by `mobile_alert_event_id`.

- [ ] When a pulled event was not already displayed, call existing local notification display helper.

- [ ] Run:

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: PASS.

- [ ] Commit:

```bash
git add apps/market-mobile/src/alertEvents.ts apps/market-mobile/App.tsx apps/market-mobile/src/notifications.ts
git commit -m "Pull missed mobile alert events on app resume"
```

---

## Phase 3: Online Delivery

### Task 5: Add SSE Stream Endpoint

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] Add endpoint:

```text
GET /api/mobile/alert-stream?push_token=<token>&after_id=<event_id>
```

- [ ] Use Server-Sent Events format:

```text
event: alert
id: 123
data: {"mobile_alert_event_id":123,"title":"...","body":"..."}
```

- [ ] First implementation may poll the DB every 2 seconds while the connection is open. Keep it simple and bounded.

- [ ] Add test for SSE payload formatting with an in-memory event list helper instead of a long-running socket.

- [ ] Commit:

```bash
git add src/market/api.py tests/test_api.py
git commit -m "Add mobile alert SSE stream"
```

### Task 6: Add Online App Listener

**Files:**
- Create: `apps/market-mobile/src/onlineAlerts.ts`
- Modify: `apps/market-mobile/App.tsx`

- [ ] Open SSE only while AppState is `active`.

- [ ] On event:
  - parse JSON
  - dedupe by `mobile_alert_event_id`
  - show in-app/local notification
  - update latest seen id

- [ ] On network error:
  - close stream
  - schedule reconnect after 3 seconds if app is active
  - call pull API before reconnect

- [ ] Run:

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: PASS.

- [ ] Commit:

```bash
git add apps/market-mobile/src/onlineAlerts.ts apps/market-mobile/App.tsx
git commit -m "Add online mobile alert stream listener"
```

### Task 7: Add ACK API

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`
- Modify: `apps/market-mobile/src/alertEvents.ts`

- [ ] Add endpoint:

```text
POST /api/mobile/alert-events/ack
```

Request:

```json
{
  "push_token": "getui:<cid>",
  "last_seen_mobile_alert_event_id": 123,
  "last_ack_mobile_alert_event_id": 123
}
```

- [ ] Server upserts `device_checkpoint`.

- [ ] App calls ACK after displaying/persisting events.

- [ ] Run:

```bash
.venv/bin/python -m unittest tests.test_api -v
cd apps/market-mobile && npm run typecheck
```

Expected: PASS.

- [ ] Commit:

```bash
git add src/market/api.py tests/test_api.py apps/market-mobile/src/alertEvents.ts
git commit -m "Acknowledge mobile alert events"
```

---

## Phase 4: Push Fallback Policy

### Task 8: Prefer Online Delivery, Fallback to Getui

**Files:**
- Modify: `src/market/mobile_delivery.py`
- Modify: `src/market/push.py`
- Modify: `tests/test_mobile_delivery.py`
- Modify: `tests/test_push.py`

- [ ] Add helper:

```python
def is_device_recently_online(
    connection: sqlite3.Connection,
    *,
    push_device_id: int,
    now_utc: str,
    freshness_seconds: int = 15,
) -> bool:
    ...
```

- [ ] Delivery policy:
  - If device has recent active SSE session, create `online` delivery and wait for ACK.
  - If no active session, send Getui immediately.
  - If online delivery is not ACKed after 10 seconds, enqueue Getui fallback.

- [ ] Keep a CLI command for push fallback:

```bash
.venv/bin/market evaluate-mobile-alerts --db-path data/market.sqlite3
```

- [ ] Run:

```bash
.venv/bin/python -m unittest tests.test_mobile_delivery tests.test_push -v
```

Expected: PASS.

- [ ] Commit:

```bash
git add src/market/mobile_delivery.py src/market/push.py tests/test_mobile_delivery.py tests/test_push.py
git commit -m "Prefer online mobile alert delivery before push fallback"
```

---

## Phase 5: Custom Indicators

### Task 9: Add Custom Indicator Definitions and Values

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/db.py`
- Create: `src/market/indicators.py`
- Create: `tests/test_indicators.py`

- [ ] Add `indicator_definition`:

```sql
CREATE TABLE IF NOT EXISTS indicator_definition (
    indicator_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    expression TEXT NOT NULL,
    input_scope TEXT NOT NULL,
    unit TEXT,
    created_by TEXT NOT NULL DEFAULT 'manual',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
```

- [ ] Add `indicator_value`:

```sql
CREATE TABLE IF NOT EXISTS indicator_value (
    indicator_value_id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator_id INTEGER NOT NULL,
    instrument_id INTEGER NOT NULL,
    value_ts_utc TEXT NOT NULL,
    value REAL NOT NULL,
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    FOREIGN KEY (indicator_id) REFERENCES indicator_definition(indicator_id),
    FOREIGN KEY (instrument_id) REFERENCES instrument(instrument_id),
    UNIQUE (indicator_id, instrument_id, value_ts_utc)
);
```

- [ ] Implement a safe expression evaluator. Allowed names:
  - `last_price`
  - `change_pct`
  - `volume_raw`
  - `turnover_raw`
  - `abs`
  - `min`
  - `max`

- [ ] Reject expressions containing attribute access, function names outside the allowlist, imports, comprehensions, lambdas, or assignment.

- [ ] Add tests:

```python
def test_evaluate_indicator_expression_allows_basic_math(self):
    self.assertEqual(
        evaluate_indicator_expression("last_price / max(volume_raw, 1)", {
            "last_price": 100.0,
            "volume_raw": 20.0,
        }),
        5.0,
    )

def test_evaluate_indicator_expression_rejects_imports(self):
    with self.assertRaises(ValueError):
        evaluate_indicator_expression("__import__('os').system('ls')", {})
```

- [ ] Commit:

```bash
git add src/market/schema.sql src/market/db.py src/market/indicators.py tests/test_indicators.py
git commit -m "Add safe custom indicator evaluation"
```

### Task 10: Let Mobile Rules Target Custom Indicators

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/db.py`
- Modify: `src/market/alerts.py`
- Modify: `src/market/api.py`
- Modify: `tests/test_alerts.py`
- Modify: `tests/test_api.py`

- [ ] Add columns to `mobile_alert_rule`:

```sql
metric_key TEXT;
indicator_id INTEGER;
source_type TEXT NOT NULL DEFAULT 'builtin';
```

- [ ] Migration rules:
  - Existing `price_above` maps to `source_type='builtin'`, `metric_key='last_price'`, operator `>`.
  - Existing `price_below` maps to `metric_key='last_price'`, operator `<`.
  - Existing `change_pct_above` maps to `metric_key='change_pct'`, operator `>`.
  - Existing `change_pct_below` maps to `metric_key='change_pct'`, operator `<`.

- [ ] Update rule evaluation:
  - builtin source reads latest `market_snapshot`
  - custom source reads latest `indicator_value`
  - all rules produce the same `mobile_alert_event` shape

- [ ] Add API support for:

```json
{
  "source_type": "custom_indicator",
  "indicator_id": 1,
  "operator": ">",
  "threshold": 2.5
}
```

- [ ] Commit:

```bash
git add src/market/schema.sql src/market/db.py src/market/alerts.py src/market/api.py tests/test_alerts.py tests/test_api.py
git commit -m "Support custom indicators in mobile alert rules"
```

---

## Phase 6: Agent API

### Task 11: Add Agent Audit Log

**Files:**
- Modify: `src/market/schema.sql`
- Modify: `src/market/db.py`
- Modify: `tests/test_api.py`

- [ ] Add table:

```sql
CREATE TABLE IF NOT EXISTS agent_action_log (
    agent_action_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    request_payload TEXT NOT NULL,
    result_payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);
```

- [ ] Every Agent API endpoint must insert one row.

- [ ] Commit:

```bash
git add src/market/schema.sql src/market/db.py tests/test_api.py
git commit -m "Add agent action audit log"
```

### Task 12: Add Agent Indicator and Rule APIs

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [ ] Add:

```text
POST /api/agent/indicators
POST /api/agent/mobile-alert-rules
POST /api/agent/mobile-alert-events
GET /api/agent/mobile-alert-events?limit=50
```

- [ ] Require header:

```text
X-Agent-Id: <agent-name>
```

- [ ] Initial safety policy:
  - Agent can create indicators.
  - Agent can create disabled draft rules by default.
  - Agent can trigger test events only if payload has `"send_push": false`.
  - Direct push from Agent remains disabled until frequency controls are in place.

- [ ] Response for draft rule creation:

```json
{
  "mobile_alert_rule_id": 10,
  "enabled": false,
  "created_by": "agent",
  "requires_user_enable": true
}
```

- [ ] Commit:

```bash
git add src/market/api.py tests/test_api.py
git commit -m "Add controlled agent alert APIs"
```

---

## Phase 7: Frequency Controls and Debugging

### Task 13: Add Rate Limits and Message Coalescing

**Files:**
- Modify: `src/market/alerts.py`
- Modify: `src/market/mobile_delivery.py`
- Modify: `tests/test_alerts.py`
- Modify: `tests/test_mobile_delivery.py`

- [x] Add per-rule cooldown enforcement for custom indicators.

- [x] Add per-device cap:

```text
max 20 normal-priority pushes per hour per device
```

- [x] Add coalescing rule:

```text
same device + same market + same symbol + same metric within 60 seconds -> one event with updated message
```

- [ ] High priority events can bypass coalescing, but still log delivery count.

- [x] Commit:

```bash
git add src/market/alerts.py src/market/mobile_delivery.py tests/test_alerts.py tests/test_mobile_delivery.py
git commit -m "Add mobile alert rate limits and coalescing"
```

### Task 14: Add Debug Payloads

**Files:**
- Modify: `src/market/api.py`
- Modify: `tests/test_api.py`

- [x] Add:

```text
GET /api/mobile/debug/push-device?push_token=<token>
GET /api/mobile/debug/deliveries?push_token=<token>&limit=50
```

- [x] Return device, latest CID, checkpoints, sessions, last events, and delivery attempts.

- [x] Commit:

```bash
git add src/market/api.py tests/test_api.py
git commit -m "Add mobile alert debug endpoints"
```

---

## Phase 8: Production Rollout

### Task 15: Deploy Server and Verify Existing Push Still Works

- [x] Run locally:

```bash
.venv/bin/python -m unittest tests.test_api tests.test_alerts tests.test_push tests.test_mobile_delivery tests.test_indicators -v
```

Expected: PASS.

- [x] Push branch:

```bash
git push origin codex-market-mobile-app
```

- [x] Deploy server:

```bash
ssh tencent-market 'cd /home/ubuntu/github/mono && git fetch origin codex-market-mobile-app && git merge --ff-only FETCH_HEAD && .venv/bin/python -m unittest tests.test_api tests.test_alerts tests.test_push -v && sudo systemctl restart market-api.service && git rev-parse --short HEAD'
```

- [x] Send one Getui test message to the known CID and confirm phone receives it.

### Task 16: Build and Install App Only After API Is Stable

- [x] Run:

```bash
cd apps/market-mobile
npm run typecheck
```

Expected: PASS.

- [x] Local Android build:

```bash
cd /Users/dt.shi/work/mono/apps/market-mobile/android
source /Users/dt.shi/work/android-local-build/env.sh
./gradlew assembleRelease
```

- [x] Install:

```bash
source /Users/dt.shi/work/android-local-build/env.sh
adb install -r /Users/dt.shi/work/mono/apps/market-mobile/android/app/build/outputs/apk/release/app-release.apk
```

- [x] Manual test:
  - Open app.
  - Confirm registration.
  - Put app foreground and trigger an alert: expect online alert.
  - Put app background and trigger an alert: expect system notification.
  - Kill app, trigger alert, reopen app: expect missed event pull.

---

## Definition of Done

- Existing Getui background push still works.
- Foreground online stream receives alert events without waiting for system push.
- App pulls missed events on startup/resume.
- Delivery attempts are tracked per event/device/channel.
- Custom indicators can be created and used by alert rules.
- Agent can create indicators and draft rules through audited APIs.
- Direct Agent push remains gated until rate limits and audit logs are in place.
- Tests cover schema, rule evaluation, indicator evaluation, API payloads, delivery outbox, and app typecheck.

---

## Recommended Execution Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9
10. Task 10
11. Task 11
12. Task 12
13. Task 13
14. Task 14
15. Task 15
16. Task 16

Do not start custom indicators or Agent APIs before the durable event/delivery/checkpoint foundation is complete.
