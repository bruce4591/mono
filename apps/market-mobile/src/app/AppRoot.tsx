import * as Notifications from "expo-notifications";
import React, { useEffect, useRef, useState } from "react";
import { AppState, StatusBar, StyleSheet, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";
import {
  acknowledgeMobileAlertEvents,
  displayNewAlertEvents,
  fetchMobileAlertEvents
} from "../alertEvents";
import { routeFromNotificationData } from "../alerts/notificationRouter";
import { initializeGetuiPush, waitForGetuiClientId } from "../getui";
import { getExpoPushToken, registerDeviceForPush } from "../notifications";
import { startOnlineAlerts } from "../onlineAlerts";
import { AlertEventsScreen } from "../screens/AlertEventsScreen";
import { AlertRulesScreen } from "../screens/AlertRulesScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { InstrumentDetailScreen } from "../screens/InstrumentDetailScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export function AppRoot() {
  const devicePushTokenRef = useRef<string | null>(null);
  const latestSeenAlertEventIdRef = useRef(0);
  const seenAlertEventIdsRef = useRef(new Set<number>());
  const [devicePushToken, setDevicePushToken] = useState<string | null>(null);
  const [getuiClientId, setGetuiClientId] = useState<string | null>(null);
  const [latestSeenAlertEventId, setLatestSeenAlertEventId] = useState(0);
  const [route, setRoute] = useState<AppRoute>(homeRoute);

  async function pullMissedAlertEvents() {
    const pushToken = devicePushTokenRef.current;
    if (!pushToken) {
      return;
    }
    const events = await fetchMobileAlertEvents({
      pushToken,
      afterId: latestSeenAlertEventIdRef.current
    });
    const maxEventId = await displayNewAlertEvents({
      events,
      seenEventIds: seenAlertEventIdsRef.current
    });
    latestSeenAlertEventIdRef.current = Math.max(latestSeenAlertEventIdRef.current, maxEventId);
    setLatestSeenAlertEventId(latestSeenAlertEventIdRef.current);
    if (maxEventId > 0) {
      await acknowledgeMobileAlertEvents({
        pushToken,
        lastSeenEventId: latestSeenAlertEventIdRef.current,
        lastAckEventId: latestSeenAlertEventIdRef.current
      });
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function registerPushChannels() {
      await initializeGetuiPush();
      const [pushToken, getuiCid] = await Promise.all([
        getExpoPushToken(),
        waitForGetuiClientId()
      ]);
      if (!cancelled) {
        await registerDeviceForPush({ pushToken, getuiCid });
        const nextToken = pushToken || (getuiCid ? `getui:${getuiCid}` : null);
        devicePushTokenRef.current = nextToken;
        setDevicePushToken(nextToken);
        setGetuiClientId(getuiCid);
        await pullMissedAlertEvents();
      }
    }

    registerPushChannels().catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        pullMissedAlertEvents().catch(() => undefined);
      }
    });
    return () => subscription.remove();
  }, []);

  useEffect(
    () =>
      startOnlineAlerts({
        getPushToken: () => devicePushTokenRef.current,
        getAfterId: () => latestSeenAlertEventIdRef.current,
        setAfterId: (eventId) => {
          latestSeenAlertEventIdRef.current = eventId;
          setLatestSeenAlertEventId(eventId);
        },
        seenEventIds: seenAlertEventIdsRef.current
      }),
    []
  );

  useEffect(() => {
    const openNotification = (response: Notifications.NotificationResponse) => {
      const data = response.notification.request.content.data as Record<string, unknown>;
      setRoute(routeFromNotificationData(data));
    };

    const lastResponse = Notifications.getLastNotificationResponse();
    if (lastResponse) {
      openNotification(lastResponse);
      Notifications.clearLastNotificationResponse();
    }

    const subscription = Notifications.addNotificationResponseReceivedListener(openNotification);
    return () => subscription.remove();
  }, []);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      {route.name === "home" ? <HomeScreen route={route} navigate={setRoute} /> : null}
      {route.name === "instrument" ? (
        <InstrumentDetailScreen route={route} navigate={setRoute} />
      ) : null}
      {route.name === "alertEvents" ? (
        <AlertEventsScreen navigate={setRoute} pushToken={devicePushToken} />
      ) : null}
      {route.name === "alertRules" ? (
        <AlertRulesScreen navigate={setRoute} pushToken={devicePushToken} />
      ) : null}
      {route.name === "settings" ? (
        <SettingsScreen
          navigate={setRoute}
          pushToken={devicePushToken}
          getuiClientId={getuiClientId}
          latestSeenAlertEventId={latestSeenAlertEventId}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  }
});
