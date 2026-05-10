import React from "react";
import { StyleSheet, Text, View } from "react-native";

import { theme } from "../theme";

export function LoadingState({ label = "加载中" }: { label?: string }) {
  return (
    <View style={styles.center}>
      <Text style={styles.text}>{label}</Text>
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
  text: {
    color: theme.colors.textMuted,
    fontSize: 15
  }
});
