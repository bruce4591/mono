import React from "react";
import { StyleSheet, Text } from "react-native";

import { theme } from "../theme";

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
    color: theme.colors.positive
  },
  negative: {
    color: theme.colors.negative
  },
  flat: {
    color: theme.colors.textMuted
  },
  muted: {
    color: theme.colors.textSubtle
  }
});
