import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";

export function AlertRulesScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  return (
    <View style={styles.root}>
      <Pressable onPress={() => navigate(homeRoute)}>
        <Text style={styles.back}>返回</Text>
      </Pressable>
      <Text style={styles.title}>提醒规则</Text>
      <Text style={styles.subtitle}>原生提醒规则列表占位</Text>
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
  subtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 15
  }
});
