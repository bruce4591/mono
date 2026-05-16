import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { fetchMobileStrategiesPayload } from "../api/strategies";
import type { MobileStrategiesPayload, MobileStrategySymbol } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { theme } from "../theme";

export function StrategiesScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  const [payload, setPayload] = useState<MobileStrategiesPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadStrategies() {
    setLoading(true);
    setError(null);
    try {
      setPayload(await fetchMobileStrategiesPayload());
    } catch (err) {
      setError(err instanceof Error ? err.message : "策略加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadStrategies();
  }, []);

  if (loading && !payload) {
    return <LoadingState label="加载策略" />;
  }

  if (error && !payload) {
    return <ErrorState message={error} onRetry={() => void loadStrategies()} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <Pressable onPress={() => void loadStrategies()}>
          <Text style={styles.refresh}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <Text style={styles.title}>策略</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <ScrollView style={styles.list}>
        {!payload || payload.strategies.length === 0 ? <EmptyState title="暂无运行策略" /> : null}
        {payload?.strategies.map((strategy) => (
          <View key={strategy.strategy_id} style={styles.strategy}>
            <View style={styles.strategyHeader}>
              <Text style={styles.strategyTitle}>{strategy.name}</Text>
              <Text style={styles.badge}>{strategy.execution_mode.toUpperCase()}</Text>
            </View>
            <Text style={styles.strategyMeta}>
              {strategy.enabled ? "运行中" : "已暂停"} · {strategy.strategy_id}
            </Text>
            {strategy.symbols.map((item) => (
              <StrategySymbolRow key={`${item.market}:${item.symbol}`} item={item} navigate={navigate} />
            ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

function StrategySymbolRow({
  item,
  navigate
}: {
  item: MobileStrategySymbol;
  navigate: (route: AppRoute) => void;
}) {
  const returnValue =
    item.position_status === "open" ? item.unrealized_return_pct : item.realized_return_pct;
  return (
    <Pressable
      style={styles.symbolRow}
      onPress={() => navigate({ name: "instrument", market: item.market, symbol: item.symbol, period: "1m" })}
    >
      <View style={styles.symbolHeader}>
        <Text style={styles.symbol}>{item.symbol}</Text>
        <Text style={[styles.status, item.position_status === "open" ? styles.statusOpen : null]}>
          {item.position_status === "open" ? "持仓中" : item.position_status === "closed" ? "已平仓" : "空仓"}
        </Text>
      </View>
      <Text style={styles.rowText}>
        入场 {formatPrice(item.entry_price)} · 当前 {formatPrice(item.current_price)}
      </Text>
      <Text style={styles.rowText}>
        收益 {formatPct(returnValue)} · 最新 {item.last_trade?.action ?? "--"}
      </Text>
    </Pressable>
  );
}

function formatPrice(value: number | null): string {
  return value === null ? "--" : value.toFixed(value > 100 ? 2 : 4);
}

function formatPct(value: number | null): string {
  return value === null ? "--" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: theme.colors.background
  },
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  back: {
    color: theme.colors.text,
    fontSize: 15
  },
  refresh: {
    color: theme.colors.text,
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: theme.colors.textStrong,
    fontSize: 24,
    fontWeight: "800"
  },
  error: {
    marginTop: 10,
    color: theme.colors.danger,
    fontSize: 13
  },
  list: {
    marginTop: 16
  },
  strategy: {
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingVertical: 14
  },
  strategyHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  strategyTitle: {
    flex: 1,
    color: theme.colors.textStrong,
    fontSize: 17,
    fontWeight: "800"
  },
  badge: {
    borderRadius: 4,
    backgroundColor: theme.colors.accent,
    paddingHorizontal: 6,
    paddingVertical: 3,
    color: theme.colors.text,
    fontSize: 11,
    fontWeight: "900",
    overflow: "hidden"
  },
  strategyMeta: {
    marginTop: 5,
    color: theme.colors.textMuted,
    fontSize: 12
  },
  symbolRow: {
    marginTop: 12,
    borderRadius: 8,
    backgroundColor: theme.colors.surface,
    padding: 12
  },
  symbolHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  symbol: {
    color: theme.colors.textStrong,
    fontSize: 16,
    fontWeight: "900"
  },
  status: {
    color: theme.colors.textMuted,
    fontSize: 12,
    fontWeight: "800"
  },
  statusOpen: {
    color: theme.colors.positive
  },
  rowText: {
    marginTop: 7,
    color: theme.colors.text,
    fontSize: 13
  }
});
