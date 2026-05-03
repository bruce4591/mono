import React, { useEffect, useRef, useState } from "react";
import { BackHandler, StatusBar, StyleSheet, View } from "react-native";
import { WebView } from "react-native-webview";
import type { WebViewNavigation } from "react-native-webview";

import { WEB_BASE_URL } from "./src/config";

export default function App() {
  const webViewRef = useRef<WebView>(null);
  const [canGoBack, setCanGoBack] = useState(false);

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
        source={{ uri: WEB_BASE_URL }}
        style={styles.webView}
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
  }
});
