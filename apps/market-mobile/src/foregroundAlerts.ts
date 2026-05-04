import { API_BASE_URL, FOREGROUND_POLL_MS } from "./config";
import { showLocalAlert } from "./notifications";

export type ForegroundAlertRule = {
  symbol: string;
  market: string;
  condition_type: "price_above" | "price_below" | "change_pct_above" | "change_pct_below";
  threshold: number;
};

type InstrumentPayload = {
  latest_snapshot?: {
    last_price?: number | null;
    change_pct?: number | null;
  };
};

const COOLDOWN_MS = 15 * 60 * 1000;

export function startForegroundAlerts(getRules: () => ForegroundAlertRule[]): () => void {
  let stopped = false;
  let timeout: ReturnType<typeof setTimeout> | null = null;
  const lastTriggered = new Map<string, number>();

  async function tick() {
    if (stopped) return;

    const rules = getRules();
    for (const rule of rules) {
      await evaluateRule(rule, lastTriggered);
    }

    if (!stopped) {
      timeout = setTimeout(() => {
        tick().catch(() => undefined);
      }, FOREGROUND_POLL_MS);
    }
  }

  tick().catch(() => undefined);

  return () => {
    stopped = true;
    if (timeout) clearTimeout(timeout);
  };
}

async function evaluateRule(
  rule: ForegroundAlertRule,
  lastTriggered: Map<string, number>
): Promise<void> {
  const response = await fetch(
    `${API_BASE_URL}/api/instruments/${encodeURIComponent(rule.market)}/${encodeURIComponent(rule.symbol)}`
  );
  if (!response.ok) return;

  const payload = (await response.json()) as InstrumentPayload;
  const snapshot = payload.latest_snapshot;
  if (!snapshot) return;

  const value = rule.condition_type.startsWith("price")
    ? Number(snapshot.last_price)
    : Number(snapshot.change_pct);
  if (!Number.isFinite(value)) return;

  const fired =
    (rule.condition_type.endsWith("above") && value > rule.threshold) ||
    (rule.condition_type.endsWith("below") && value < rule.threshold);
  if (!fired) return;

  const key = `${rule.market}:${rule.symbol}:${rule.condition_type}:${rule.threshold}`;
  const now = Date.now();
  if (now - (lastTriggered.get(key) ?? 0) <= COOLDOWN_MS) return;

  lastTriggered.set(key, now);
  await showLocalAlert(`${rule.symbol} 提醒`, `当前值 ${value}, 阈值 ${rule.threshold}`);
}
