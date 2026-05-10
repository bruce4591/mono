import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { theme } from "../theme";

export function ErrorState({
  title = "加载失败",
  message,
  onRetry
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <View style={styles.center}>
      <Text style={styles.title}>{title}</Text>
      {message ? <Text style={styles.message}>{message}</Text> : null}
      {onRetry ? (
        <Pressable style={styles.button} onPress={onRetry}>
          <Text style={styles.buttonText}>重试</Text>
        </Pressable>
      ) : null}
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
    color: theme.colors.textStrong,
    fontSize: 18,
    fontWeight: "700",
    textAlign: "center"
  },
  message: {
    marginTop: 8,
    color: theme.colors.textMuted,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  },
  button: {
    marginTop: 18,
    borderRadius: 8,
    backgroundColor: theme.colors.accent,
    paddingHorizontal: 16,
    paddingVertical: 10
  },
  buttonText: {
    color: theme.colors.text,
    fontSize: 15,
    fontWeight: "800"
  }
});
