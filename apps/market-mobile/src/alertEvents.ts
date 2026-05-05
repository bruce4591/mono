import { API_BASE_URL } from "./config";
import { showLocalAlert } from "./notifications";

export type MobileAlertEvent = {
  mobile_alert_event_id: number;
  title: string;
  body: string;
  data?: Record<string, unknown>;
};

type AlertEventsResponse = {
  events?: MobileAlertEvent[];
};

export async function fetchMobileAlertEvents({
  pushToken,
  afterId
}: {
  pushToken: string;
  afterId: number;
}): Promise<MobileAlertEvent[]> {
  const url = new URL(`${API_BASE_URL}/api/mobile/alert-events`);
  url.searchParams.set("push_token", pushToken);
  url.searchParams.set("after_id", String(afterId));

  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error("Failed to fetch mobile alert events");
  }

  const payload = (await response.json()) as AlertEventsResponse;
  return Array.isArray(payload.events) ? payload.events : [];
}

export async function displayNewAlertEvents({
  events,
  seenEventIds
}: {
  events: MobileAlertEvent[];
  seenEventIds: Set<number>;
}): Promise<number> {
  let maxEventId = 0;
  for (const event of events) {
    maxEventId = Math.max(maxEventId, event.mobile_alert_event_id);
    if (seenEventIds.has(event.mobile_alert_event_id)) {
      continue;
    }
    seenEventIds.add(event.mobile_alert_event_id);
    await showLocalAlert(event.title, event.body, event.data);
  }
  return maxEventId;
}

export async function acknowledgeMobileAlertEvents({
  pushToken,
  lastSeenEventId,
  lastAckEventId
}: {
  pushToken: string;
  lastSeenEventId: number;
  lastAckEventId: number;
}): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/mobile/alert-events/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      push_token: pushToken,
      last_seen_mobile_alert_event_id: lastSeenEventId,
      last_ack_mobile_alert_event_id: lastAckEventId
    })
  });
  if (!response.ok) {
    throw new Error("Failed to acknowledge mobile alert events");
  }
}
