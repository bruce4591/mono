import React, { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";
import { fetchMobileHome } from "../api/market";
import type { MobileHomePayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";

export function HomeScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  const [payload, setPayload] = useState<MobileHomePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadHome(force = false) {
    setLoading(true);
    setError(null);
    try {
      const nextPayload = await getCached({
        key: "mobile-home",
        ttlMs: 60_000,
        load: fetchMobileHome,
        force
      });
      setPayload(nextPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "首页加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadHome();
  }, []);

  if (loading && !payload) {
    return <LoadingState label="加载排行榜" />;
  }

  if (error && !payload) {
    return <ErrorState message={error} onRetry={() => void loadHome(true)} />;
  }

  return (
    <View style={styles.root}>
      <Text style={styles.title}>Market</Text>
      <Text style={styles.subtitle}>原生排行榜页面</Text>
      <Text style={styles.meta}>
        {payload ? `榜单 ${payload.boards.length} 个` : "榜单 --"}
      </Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.actions}>
        <Pressable onPress={() => navigate({ name: "instrument", market: "HK", symbol: "09988" })}>
          <Text style={styles.link}>打开 HK 09988</Text>
        </Pressable>
        <Pressable onPress={() => void loadHome(true)}>
          <Text style={styles.link}>刷新</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "alertEvents" })}>
          <Text style={styles.link}>提醒事件</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "alertRules" })}>
          <Text style={styles.link}>提醒规则</Text>
        </Pressable>
        <Pressable onPress={() => navigate({ name: "settings" })}>
          <Text style={styles.link}>设置</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: "#071113"
  },
  title: {
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  subtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 15
  },
  meta: {
    marginTop: 16,
    color: "#d6e2e4",
    fontSize: 14
  },
  error: {
    marginTop: 10,
    color: "#fca5a5",
    fontSize: 13
  },
  actions: {
    gap: 14,
    marginTop: 24
  },
  link: {
    color: "#5eead4",
    fontSize: 16
  }
});
