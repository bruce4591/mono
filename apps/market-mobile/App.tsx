import * as Notifications from "expo-notifications";
import React, { useEffect, useRef, useState } from "react";
import { BackHandler, StatusBar, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import type { WebViewNavigation } from "react-native-webview";

import { WEB_BASE_URL } from "./src/config";
import { startForegroundAlerts } from "./src/foregroundAlerts";
import { registerDeviceForPush } from "./src/notifications";

function notificationUrlFromData(data: Record<string, unknown>): string {
  const url = typeof data.url === "string" ? data.url : null;
  if (url?.startsWith("/")) return `${WEB_BASE_URL}${url}`;
  if (url?.startsWith(WEB_BASE_URL)) return url;

  const market = typeof data.market === "string" ? data.market : null;
  const symbol = typeof data.symbol === "string" ? data.symbol : null;
  if (market && symbol) {
    return `${WEB_BASE_URL}/instrument.html?market=${encodeURIComponent(market)}&symbol=${encodeURIComponent(symbol)}`;
  }

  return `${WEB_BASE_URL}/status.html`;
}

export default function App() {
  const webViewRef = useRef<WebView>(null);
  const [sourceUri, setSourceUri] = useState(WEB_BASE_URL);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loadError, setLoadError] = useState<{
    code?: number;
    description?: string;
    url?: string;
  } | null>(null);

  useEffect(() => {
    registerDeviceForPush().catch(() => undefined);
  }, []);

  useEffect(() => startForegroundAlerts(() => []), []);

  useEffect(() => {
    const openNotification = (response: Notifications.NotificationResponse) => {
      const data = response.notification.request.content.data as Record<string, unknown>;
      setSourceUri(notificationUrlFromData(data));
    };

    const lastResponse = Notifications.getLastNotificationResponse();
    if (lastResponse) {
      openNotification(lastResponse);
      Notifications.clearLastNotificationResponse();
    }

    const subscription = Notifications.addNotificationResponseReceivedListener(openNotification);
    return () => subscription.remove();
  }, []);

  useEffect(() => {
    const subscription = BackHandler.addEventListener("hardwareBackPress", () => {
      if (canGoBack) {
        webViewRef.current?.goBack();
        return true;
      }

      return false;
    });

    return () => subscription.remove();
  }, [canGoBack]);

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor="#071113" />
      <WebView
        ref={webViewRef}
        source={{ uri: sourceUri }}
        style={styles.webView}
        originWhitelist={["http://*", "https://*"]}
        mixedContentMode="always"
        onLoadStart={() => {
          setLoadError(null);
        }}
        onError={(event) => {
          const { code, description, url } = event.nativeEvent;
          setLoadError({ code, description, url });
        }}
        renderError={(domain, code, description) => (
          <View style={styles.error}>
            <Text style={styles.errorTitle}>Error loading page</Text>
            <Text style={styles.errorText}>Target: {sourceUri}</Text>
            {loadError?.url ? <Text style={styles.errorText}>URL: {loadError.url}</Text> : null}
            <Text style={styles.errorText}>Domain: {domain ?? "undefined"}</Text>
            <Text style={styles.errorText}>Error Code: {code}</Text>
            <Text style={styles.errorText}>Description: {description}</Text>
          </View>
        )}
        onNavigationStateChange={(state: WebViewNavigation) => {
          setCanGoBack(state.canGoBack);
        }}
        sharedCookiesEnabled
        allowsBackForwardNavigationGestures
      />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  },
  webView: {
    flex: 1,
    backgroundColor: "#071113"
  },
  error: {
    flex: 1,
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#071113"
  },
  errorTitle: {
    marginBottom: 16,
    color: "#f8fafc",
    fontSize: 22,
    fontWeight: "700",
    textAlign: "center"
  },
  errorText: {
    marginTop: 8,
    color: "#cbd5e1",
    fontSize: 15,
    lineHeight: 22,
    textAlign: "center"
  }
});
