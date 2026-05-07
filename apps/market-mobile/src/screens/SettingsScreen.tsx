import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { API_BASE_URL } from "../config";

export function SettingsScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  return (
    <View style={styles.root}>
      <Pressable onPress={() => navigate(homeRoute)}>
        <Text style={styles.back}>返回</Text>
      </Pressable>
      <Text style={styles.title}>设置</Text>
      <Text style={styles.label}>API</Text>
      <Text style={styles.value}>{API_BASE_URL}</Text>
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
  back: {
    color: "#5eead4",
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  label: {
    marginTop: 24,
    color: "#9fb2b7",
    fontSize: 13
  },
  value: {
    marginTop: 6,
    color: "#d6e2e4",
    fontSize: 14,
    lineHeight: 20
  }
});
