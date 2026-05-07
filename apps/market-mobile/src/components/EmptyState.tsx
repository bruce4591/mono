import React from "react";
import { StyleSheet, Text, View } from "react-native";

export function EmptyState({
  title = "暂无数据",
  message
}: {
  title?: string;
  message?: string;
}) {
  return (
    <View style={styles.center}>
      <Text style={styles.title}>{title}</Text>
      {message ? <Text style={styles.message}>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24
  },
  title: {
    color: "#f8fafc",
    fontSize: 18,
    fontWeight: "700",
    textAlign: "center"
  },
  message: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  }
});
