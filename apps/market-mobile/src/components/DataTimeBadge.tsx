import React from "react";
import { StyleSheet, Text } from "react-native";

export function DataTimeBadge({ value }: { value: string | null }) {
  return <Text style={styles.badge}>{value ? `数据 ${formatDataTime(value)}` : "数据 --"}</Text>;
}

function formatDataTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  });
}

const styles = StyleSheet.create({
  badge: {
    color: "#8ea4aa",
    fontSize: 12
  }
});
