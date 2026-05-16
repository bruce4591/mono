import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { fetchMobileStrategiesPayload } from "../api/strategies";
import type { MobileStrategy } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { theme } from "../theme";

export function AlertRulesScreen({
  navigate,
  pushToken
}: {
  navigate: (route: AppRoute) => void;
  pushToken: string | null;
}) {
  const [strategies, setStrategies] = useState<MobileStrategy[]>([]);
  const [loading, setLoading] = useState(Boolean(pushToken));
  const [error, setError] = useState<string | null>(null);

  async function loadRules() {
    if (!pushToken) return;
    setLoading(true);
    setError(null);
    try {
      const strategyPayload = await fetchMobileStrategiesPayload();
      setStrategies(strategyPayload.strategies);
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略提醒加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadRules();
  }, [pushToken]);

  if (!pushToken) {
    return (
      <View style={styles.root}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <EmptyState title="等待设备注册" message="推送 token 注册完成后会显示提醒规则" />
      </View>
    );
  }

  if (loading && strategies.length === 0) {
    return <LoadingState label="加载策略提醒" />;
  }

  if (error && strategies.length === 0) {
    return <ErrorState message={error} onRetry={() => void loadRules()} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <Pressable onPress={() => void loadRules()}>
          <Text style={styles.refresh}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <Text style={styles.title}>策略提醒</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <ScrollView style={styles.list}>
        <View style={styles.strategySection}>
          <Text style={styles.sectionTitle}>后台运行策略</Text>
          {strategies.length === 0 ? (
            <Text style={styles.itemMeta}>暂无后台策略</Text>
          ) : null}
          {strategies.map((strategy) => (
            <Pressable
              key={strategy.strategy_id}
              style={styles.strategySummary}
              onPress={() => navigate({ name: "strategies" })}
            >
              <View style={styles.strategySummaryHeader}>
                <Text style={styles.strategyName}>{strategy.name}</Text>
                <Text style={styles.strategyBadge}>{strategy.enabled ? "后台运行" : "已暂停"}</Text>
              </View>
              <Text style={styles.itemBody}>{strategy.description}</Text>
              <Text style={styles.itemMeta}>
                {strategy.symbols.map((item) => item.symbol).join(" / ") || "--"} ·{" "}
                {strategy.execution_mode.toUpperCase()}
              </Text>
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: theme.colors.background
  },
  back: {
    color: theme.colors.text,
    fontSize: 15
  },
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  refresh: {
    color: theme.colors.text,
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: theme.colors.textStrong,
    fontSize: 24,
    fontWeight: "700"
  },
  error: {
    marginTop: 10,
    color: theme.colors.danger,
    fontSize: 13
  },
  list: {
    marginTop: 18
  },
  strategySection: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.borderStrong,
    paddingBottom: 14
  },
  sectionTitle: {
    color: theme.colors.textStrong,
    fontSize: 16,
    fontWeight: "800"
  },
  strategySummary: {
    marginTop: 10,
    borderRadius: 8,
    backgroundColor: theme.colors.surface,
    padding: 12
  },
  strategySummaryHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  strategyName: {
    flex: 1,
    color: theme.colors.textStrong,
    fontSize: 15,
    fontWeight: "800"
  },
  strategyBadge: {
    color: theme.colors.positive,
    fontSize: 12,
    fontWeight: "900"
  },
  itemBody: {
    marginTop: 5,
    color: theme.colors.text,
    fontSize: 14
  },
  itemMeta: {
    marginTop: 7,
    color: theme.colors.textMuted,
    fontSize: 12
  }
});
