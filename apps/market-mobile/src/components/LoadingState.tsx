import React from "react";
import { StyleSheet, Text, View } from "react-native";

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
    color: "#d6e2e4",
    fontSize: 15
  }
});
