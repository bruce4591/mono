import { AppState } from "react-native";

import {
  acknowledgeMobileAlertEvents,
  displayNewAlertEvents,
  type MobileAlertEvent
} from "./alertEvents";
import { API_BASE_URL } from "./config";

const SSE_RECONNECT_MS = 3000;

export function startOnlineAlerts({
  getPushToken,
  getAfterId,
  setAfterId,
  seenEventIds
}: {
  getPushToken: () => string | null;
  getAfterId: () => number;
  setAfterId: (eventId: number) => void;
  seenEventIds: Set<number>;
}): () => void {
  let stopped = false;
  let xhr: XMLHttpRequest | null = null;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;

  const stopConnection = () => {
    if (xhr) {
      xhr.abort();
      xhr = null;
    }
  };

  const scheduleConnect = () => {
    if (stopped || reconnectTimeout) return;
    reconnectTimeout = setTimeout(() => {
      reconnectTimeout = null;
      connect();
    }, SSE_RECONNECT_MS);
  };

  const connect = () => {
    if (stopped || AppState.currentState !== "active" || xhr) return;

    const pushToken = getPushToken();
    if (!pushToken) {
      scheduleConnect();
      return;
    }

    let processedLength = 0;
    let buffer = "";
    const streamUrl = new URL(`${API_BASE_URL}/api/mobile/alert-stream`);
    streamUrl.searchParams.set("push_token", pushToken);
    streamUrl.searchParams.set("after_id", String(getAfterId()));

    xhr = new XMLHttpRequest();
    xhr.open("GET", streamUrl.toString(), true);
    xhr.setRequestHeader("Accept", "text/event-stream");

    xhr.onprogress = () => {
      if (!xhr) return;
      const chunk = xhr.responseText.slice(processedLength);
      processedLength = xhr.responseText.length;
      buffer = processSseChunk(buffer + chunk, async (event) => {
        const maxEventId = await displayNewAlertEvents({
          events: [event],
          seenEventIds
        });
        if (maxEventId <= 0) return;
        const nextEventId = Math.max(getAfterId(), maxEventId);
        setAfterId(nextEventId);
        await acknowledgeMobileAlertEvents({
          pushToken,
          lastSeenEventId: nextEventId,
          lastAckEventId: nextEventId
        });
      });
    };

    xhr.onerror = () => {
      xhr = null;
      scheduleConnect();
    };

    xhr.onload = () => {
      xhr = null;
      scheduleConnect();
    };

    xhr.send();
  };

  const appStateSubscription = AppState.addEventListener("change", (state) => {
    if (state === "active") {
      connect();
    } else {
      stopConnection();
    }
  });

  connect();

  return () => {
    stopped = true;
    appStateSubscription.remove();
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    stopConnection();
  };
}

export function processSseChunk(
  buffer: string,
  onAlert: (event: MobileAlertEvent) => void | Promise<void>
): string {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const remainder = parts.pop() ?? "";
  for (const part of parts) {
    const event = parseSseEvent(part);
    if (event) {
      void onAlert(event);
    }
  }
  return remainder;
}

function parseSseEvent(block: string): MobileAlertEvent | null {
  const lines = block.split("\n");
  let eventType = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (eventType !== "alert" || dataLines.length === 0) {
    return null;
  }
  const parsed = JSON.parse(dataLines.join("\n")) as MobileAlertEvent;
  if (typeof parsed.mobile_alert_event_id !== "number") {
    return null;
  }
  return parsed;
}
