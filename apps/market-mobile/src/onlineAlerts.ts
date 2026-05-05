import { AppState } from "react-native";

import {
  acknowledgeMobileAlertEvents,
  displayNewAlertEvents,
  fetchMobileAlertEvents
} from "./alertEvents";

const ONLINE_ALERT_POLL_MS = 3000;

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
  let timeout: ReturnType<typeof setTimeout> | null = null;

  async function tick() {
    if (stopped) return;

    const pushToken = getPushToken();
    if (pushToken && AppState.currentState === "active") {
      const events = await fetchMobileAlertEvents({
        pushToken,
        afterId: getAfterId()
      });
      const maxEventId = await displayNewAlertEvents({ events, seenEventIds });
      if (maxEventId > 0) {
        const nextEventId = Math.max(getAfterId(), maxEventId);
        setAfterId(nextEventId);
        await acknowledgeMobileAlertEvents({
          pushToken,
          lastSeenEventId: nextEventId,
          lastAckEventId: nextEventId
        });
      }
    }

    if (!stopped) {
      timeout = setTimeout(() => {
        tick().catch(() => undefined);
      }, ONLINE_ALERT_POLL_MS);
    }
  }

  tick().catch(() => undefined);

  return () => {
    stopped = true;
    if (timeout) clearTimeout(timeout);
  };
}
