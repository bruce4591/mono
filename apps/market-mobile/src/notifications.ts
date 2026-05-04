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
}: PushRegistration): Promise<void> {
  if (!pushToken && !getuiCid) {
    return;
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
}

export async function showLocalAlert(title: string, body: string): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: { title, body, sound: true },
    trigger: null
  });
}
