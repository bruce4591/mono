import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { API_BASE_URL } from "./config";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false
  })
});

export type PushRegistration = {
  pushToken: string | null;
  getuiCid?: string | null;
};

export type PushRegistrationResult = {
  lastSeenAlertEventId: number;
  lastAckAlertEventId: number;
};

async function ensureNotificationPermission(): Promise<boolean> {
  const current = await Notifications.getPermissionsAsync();
  const permission =
    current.status === "granted" ? current : await Notifications.requestPermissionsAsync();

  if (permission.status !== "granted") {
    return false;
  }

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("market-alerts", {
      name: "Market Alerts",
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: "#22c55e"
    });
  }

  return true;
}

export async function getExpoPushToken(): Promise<string | null> {
  const hasPermission = await ensureNotificationPermission();
  if (!hasPermission) {
    return null;
  }

  try {
    const token = await Notifications.getExpoPushTokenAsync();
    return token.data;
  } catch {
    return null;
  }
}

export async function registerDeviceForPush({
  pushToken,
  getuiCid
}: PushRegistration): Promise<PushRegistrationResult> {
  if (!pushToken && !getuiCid) {
    return { lastSeenAlertEventId: 0, lastAckAlertEventId: 0 };
  }

  const response = await fetch(`${API_BASE_URL}/api/mobile/devices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform: Platform.OS,
      push_token: pushToken || undefined,
      getui_cid: getuiCid || undefined,
      device_label: "OnePlus 13T"
    })
  });

  if (!response.ok) {
    throw new Error("Failed to register push token");
  }
  const payload = (await response.json()) as {
    checkpoint?: {
      last_seen_mobile_alert_event_id?: unknown;
      last_ack_mobile_alert_event_id?: unknown;
    } | null;
  };
  return {
    lastSeenAlertEventId:
      typeof payload.checkpoint?.last_seen_mobile_alert_event_id === "number"
        ? payload.checkpoint.last_seen_mobile_alert_event_id
        : 0,
    lastAckAlertEventId:
      typeof payload.checkpoint?.last_ack_mobile_alert_event_id === "number"
        ? payload.checkpoint.last_ack_mobile_alert_event_id
        : 0
  };
}

export async function showLocalAlert(
  title: string,
  body: string,
  data?: Record<string, unknown>
): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: { title, body, data, sound: true },
    trigger: null
  });
}
