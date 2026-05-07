import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";

export function HomeScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  return (
    <View style={styles.root}>
      <Text style={styles.title}>Market</Text>
      <Text style={styles.subtitle}>原生排行榜页面</Text>
      <View style={styles.actions}>
        <Pressable onPress={() => navigate({ name: "instrument", market: "HK", symbol: "09988" })}>
          <Text style={styles.link}>打开 HK 09988</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "alertEvents" })}>
          <Text style={styles.link}>提醒事件</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "alertRules" })}>
          <Text style={styles.link}>提醒规则</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "settings" })}>
          <Text style={styles.link}>设置</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: "#071113"
  },
  title: {
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  subtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 15
  },
  actions: {
    gap: 14,
    marginTop: 24
  },
  link: {
    color: "#5eead4",
    fontSize: 16
  }
});
