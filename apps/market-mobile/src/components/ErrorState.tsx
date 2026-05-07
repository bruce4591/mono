import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

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
  },
  button: {
    marginTop: 18,
    borderRadius: 8,
    backgroundColor: "#123638",
    paddingHorizontal: 16,
    paddingVertical: 10
  },
  buttonText: {
    color: "#dff8f5",
    fontSize: 15,
    fontWeight: "600"
  }
});
