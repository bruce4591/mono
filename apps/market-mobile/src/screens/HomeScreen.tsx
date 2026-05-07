import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";
import { fetchMobileHome } from "../api/market";
import type { MobileHomePayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { MarketList } from "../components/MarketList";

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
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Market</Text>
          <Text style={styles.subtitle}>
            {payload ? `榜单 ${payload.boards.length} 个` : "榜单 --"}
          </Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable onPress={() => navigate({ name: "alertEvents" })}>
            <Text style={styles.link}>提醒</Text>
          </Pressable>
          <Pressable onPress={() => navigate({ name: "settings" })}>
            <Text style={styles.link}>设置</Text>
          </Pressable>
        </View>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable style={styles.refreshButton} onPress={() => void loadHome(true)}>
        <Text style={styles.refreshText}>{loading ? "刷新中" : "刷新"}</Text>
      </Pressable>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <MarketList boards={payload?.boards ?? []} navigate={navigate} />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  },
  header: {
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingTop: 48,
    paddingBottom: 14
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
  headerActions: {
    flexDirection: "row",
    gap: 16
  },
  error: {
    paddingHorizontal: 16,
    paddingBottom: 8,
    color: "#fca5a5",
    fontSize: 13
  },
  link: {
    color: "#5eead4",
    fontSize: 16
  },
  refreshButton: {
    marginHorizontal: 16,
    marginBottom: 8,
    alignSelf: "flex-start",
    borderRadius: 8,
    backgroundColor: "#123638",
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  refreshText: {
    color: "#dff8f5",
    fontSize: 14,
    fontWeight: "600"
  },
  scroll: {
    flex: 1
  },
  scrollContent: {
    paddingHorizontal: 16
  }
});
