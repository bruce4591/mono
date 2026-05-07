import React, { useState } from "react";
import { StatusBar, StyleSheet, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";
import { AlertEventsScreen } from "../screens/AlertEventsScreen";
import { AlertRulesScreen } from "../screens/AlertRulesScreen";
import { HomeScreen } from "../screens/HomeScreen";
import { InstrumentDetailScreen } from "../screens/InstrumentDetailScreen";
import { SettingsScreen } from "../screens/SettingsScreen";

export function AppRoot() {
  const [route, setRoute] = useState<AppRoute>(homeRoute);

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
