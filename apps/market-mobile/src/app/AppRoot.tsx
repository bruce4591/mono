import * as Notifications from "expo-notifications";
import React, { useEffect, useState } from "react";
import { StatusBar, StyleSheet, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";
import { routeFromNotificationData } from "../alerts/notificationRouter";
import { AlertEventsScreen } from "../screens/AlertEventsScreen";
import { AlertRulesScreen } from "../screens/AlertRulesScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { InstrumentDetailScreen } from "../screens/InstrumentDetailScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export function AppRoot() {
  const [route, setRoute] = useState<AppRoute>(homeRoute);

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
      {route.name === "home" ? <HomeScreen navigate={setRoute} /> : null}
      {route.name === "instrument" ? (
        <InstrumentDetailScreen route={route} navigate={setRoute} />
      ) : null}
      {route.name === "alertEvents" ? <AlertEventsScreen navigate={setRoute} /> : null}
      {route.name === "alertRules" ? <AlertRulesScreen navigate={setRoute} /> : null}
      {route.name === "settings" ? <SettingsScreen navigate={setRoute} /> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  }
});
