import * as Notifications from "expo-notifications";
import React, { useEffect, useRef, useState } from "react";
import { AppState, StatusBar, StyleSheet, View } from "react-native";

import { homeRoute, type AppRoute, type HomeRoute } from "./navigation";
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
import { StrategiesScreen } from "../screens/StrategiesScreen";
import { theme } from "../theme";

export function AppRoot() {
  const devicePushTokenRef = useRef<string | null>(null);
  const latestSeenAlertEventIdRef = useRef(0);
  const seenAlertEventIdsRef = useRef(new Set<number>());
  const [devicePushToken, setDevicePushToken] = useState<string | null>(null);
  const [getuiClientId, setGetuiClientId] = useState<string | null>(null);
  const [latestSeenAlertEventId, setLatestSeenAlertEventId] = useState(0);
  const [readAlertEventIds, setReadAlertEventIds] = useState<Set<number>>(new Set());
  const lastHomeRouteRef = useRef<HomeRoute>(homeRoute);
  const [route, setRoute] = useState<AppRoute>(homeRoute);

  function updateLastHomeRoute(nextRoute: HomeRoute) {
    lastHomeRouteRef.current = nextRoute;
  }

  function navigate(nextRoute: AppRoute) {
    if (nextRoute.name === "home") {
      const targetRoute =
        nextRoute.group || nextRoute.boardKey !== undefined ? nextRoute : lastHomeRouteRef.current;
      updateLastHomeRoute(targetRoute);
      setRoute(targetRoute);
      return;
    }
    if (nextRoute.name === "instrument" && !nextRoute.returnTo) {
      setRoute({ ...nextRoute, returnTo: lastHomeRouteRef.current });
      return;
    }
    setRoute(nextRoute);
  }

  function markAlertEventRead(eventId: number) {
    setReadAlertEventIds((previous) => {
      if (previous.has(eventId)) {
        return previous;
      }
      const next = new Set(previous);
      next.add(eventId);
      return next;
    });
  }

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
        const registration = await registerDeviceForPush({ pushToken, getuiCid });
        const nextToken = pushToken || (getuiCid ? `getui:${getuiCid}` : null);
        latestSeenAlertEventIdRef.current = Math.max(
          latestSeenAlertEventIdRef.current,
          registration.lastSeenAlertEventId,
          registration.lastAckAlertEventId
        );
        devicePushTokenRef.current = nextToken;
        setDevicePushToken(nextToken);
        setGetuiClientId(getuiCid);
        setLatestSeenAlertEventId(latestSeenAlertEventIdRef.current);
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
      const eventId =
        typeof data.mobile_alert_event_id === "number"
          ? data.mobile_alert_event_id
          : typeof data.mobile_alert_event_id === "string"
            ? Number.parseInt(data.mobile_alert_event_id, 10)
            : null;
      if (eventId !== null && Number.isFinite(eventId)) {
        markAlertEventRead(eventId);
      }
      navigate(routeFromNotificationData(data));
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
      <StatusBar barStyle="dark-content" backgroundColor={theme.colors.background} />
      {route.name === "home" ? (
        <HomeScreen
          route={route}
          navigate={navigate}
          onRouteStateChange={updateLastHomeRoute}
        />
      ) : null}
      {route.name === "instrument" ? (
        <InstrumentDetailScreen route={route} navigate={navigate} />
      ) : null}
      {route.name === "alertEvents" ? (
        <AlertEventsScreen
          navigate={navigate}
          pushToken={devicePushToken}
          readEventIds={readAlertEventIds}
          onReadEvent={markAlertEventRead}
        />
      ) : null}
      {route.name === "alertRules" ? (
        <AlertRulesScreen navigate={navigate} pushToken={devicePushToken} />
      ) : null}
      {route.name === "strategies" ? <StrategiesScreen navigate={navigate} /> : null}
      {route.name === "settings" ? (
        <SettingsScreen
          navigate={navigate}
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
    backgroundColor: theme.colors.background
  }
});
