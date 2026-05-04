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

export async function registerDeviceForPush(getuiCid?: string | null): Promise<string | null> {
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
  const response = await fetch(`${API_BASE_URL}/api/mobile/devices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      platform: Platform.OS,
      push_token: token.data,
      getui_cid: getuiCid || undefined,
      device_label: "OnePlus 13T"
    })
  });

  if (!response.ok) {
    throw new Error("Failed to register push token");
  }

  return token.data;
}

export async function showLocalAlert(title: string, body: string): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: { title, body, sound: true },
    trigger: null
  });
}
