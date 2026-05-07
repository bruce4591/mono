import React, { useState } from "react";
import { Pressable, StatusBar, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "./navigation";

export function AppRoot() {
  const [route, setRoute] = useState<AppRoute>(homeRoute);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      <Text style={styles.title}>Market</Text>
      <Text style={styles.subtitle}>原生 App 正在接管页面，当前路由：{route.name}</Text>
      <View style={styles.actions}>
        <Pressable style={styles.button} onPress={() => setRoute(homeRoute)}>
          <Text style={styles.buttonText}>首页</Text>
        </Pressable>
        <Pressable style={styles.button} onPress={() => setRoute({ name: "alertEvents" })}>
          <Text style={styles.buttonText}>提醒</Text>
        </Pressable>
        <Pressable style={styles.button} onPress={() => setRoute({ name: "settings" })}>
          <Text style={styles.buttonText}>设置</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 56,
    backgroundColor: "#071113"
  },
  title: {
    color: "#f8fafc",
    fontSize: 26,
    fontWeight: "700"
  },
  subtitle: {
    marginTop: 10,
    color: "#9fb2b7",
    fontSize: 15,
    lineHeight: 22
  },
  actions: {
    flexDirection: "row",
    gap: 10,
    marginTop: 24
  },
  button: {
    borderRadius: 8,
    backgroundColor: "#123638",
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  buttonText: {
    color: "#dff8f5",
    fontSize: 15,
    fontWeight: "600"
  }
});
