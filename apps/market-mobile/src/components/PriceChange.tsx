import React from "react";
import { StyleSheet, Text } from "react-native";

export function PriceChange({ value }: { value: number | null }) {
  const colorStyle =
    value === null ? styles.muted : value > 0 ? styles.positive : value < 0 ? styles.negative : styles.flat;
  const label = value === null ? "--" : `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
  return <Text style={[styles.value, colorStyle]}>{label}</Text>;
}

const styles = StyleSheet.create({
  value: {
    minWidth: 70,
    fontSize: 14,
    fontWeight: "700",
    textAlign: "right"
  },
  positive: {
    color: "#f87171"
  },
  negative: {
    color: "#34d399"
  },
  flat: {
    color: "#d6e2e4"
  },
  muted: {
    color: "#6f858a"
  }
});
