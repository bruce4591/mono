import { fetchJson } from "./client";
import type { MobileAlertEventsPayload, MobileAlertRulesPayload } from "./types";

export function fetchMobileAlertEventsPayload({
  pushToken,
  afterId
}: {
  pushToken: string;
  afterId: number;
}): Promise<MobileAlertEventsPayload> {
  const params = new URLSearchParams({
    push_token: pushToken,
    after_id: String(afterId)
  });
  return fetchJson<MobileAlertEventsPayload>(`/api/mobile/alert-events?${params}`);
}

export function acknowledgeMobileAlertEventsPayload({
  pushToken,
  lastSeenEventId,
  lastAckEventId
}: {
  pushToken: string;
  lastSeenEventId: number;
  lastAckEventId: number;
}): Promise<void> {
  return fetchJson<void>("/api/mobile/alert-events/ack", {
    method: "POST",
    body: JSON.stringify({
      push_token: pushToken,
      last_seen_mobile_alert_event_id: lastSeenEventId,
      last_ack_mobile_alert_event_id: lastAckEventId
    })
  });
}

export function fetchMobileAlertRulesPayload({
  pushToken
}: {
  pushToken: string;
}): Promise<MobileAlertRulesPayload> {
  const params = new URLSearchParams({ push_token: pushToken });
  return fetchJson<MobileAlertRulesPayload>(`/api/mobile/alert-rules?${params}`);
}
