import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute, type InstrumentRoute } from "../app/navigation";
import { fetchMobileInstrumentDetail } from "../api/market";
import type { MobileInstrumentDetailPayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { NativeKLineChart } from "../components/NativeKLineChart";

const PERIOD_TABS = [
  { label: "分时", value: "1m" },
  { label: "15分", value: "15m" },
  { label: "5分", value: "5m" },
  { label: "8小时", value: "8h" },
  { label: "1天", value: "1d" }
];

export function InstrumentDetailScreen({
  route,
  navigate
}: {
  route: InstrumentRoute;
  navigate: (route: AppRoute) => void;
}) {
  const [period, setPeriod] = useState("1d");
  const [payload, setPayload] = useState<MobileInstrumentDetailPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function loadDetail(force = false) {
    setLoading(true);
    setError(null);
    try {
      const nextPayload = await getCached({
        key: `instrument:${route.market}:${route.symbol}:${period}`,
        ttlMs: 60_000,
        load: () =>
          fetchMobileInstrumentDetail({
            market: route.market,
            symbol: route.symbol,
            period
          }),
        force
      });
      setPayload(nextPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "详情加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDetail();
  }, [route.market, route.symbol, period]);

  if (loading && !payload) {
    return <LoadingState label="加载详情" />;
  }

  if (error && !payload) {
    return <ErrorState message={error} onRetry={() => void loadDetail(true)} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <Pressable style={styles.iconButton} onPress={() => navigate(homeRoute)}>
          <Text style={styles.iconText}>‹</Text>
        </Pressable>
        <View style={styles.titleBlock}>
          <View style={styles.symbolRow}>
            <Text style={styles.symbol}>{route.symbol}</Text>
            <Text style={styles.badge}>{route.market}</Text>
          </View>
          <Text style={styles.name} numberOfLines={1}>
            {payload?.instrument.name || `${route.market} ${route.symbol}`}
          </Text>
        </View>
        <Pressable style={styles.refreshButton} onPress={() => void loadDetail(true)}>
          <Text style={styles.refreshText}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <View style={styles.statsRow}>
        <Metric label="标记价格" value={formatNumber(payload?.snapshot.last_price ?? null)} />
        <Metric label="涨跌幅" value={formatPercent(payload?.snapshot.change_pct ?? null)} />
        <Metric label="成交额" value={formatMarketMetric(payload?.snapshot.turnover ?? null)} />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.periods}>
        {PERIOD_TABS.filter((item) => payload?.periods.includes(item.value) || item.value === period).map((item) => {
          return (
            <Pressable
              key={item.value}
              style={styles.periodPressable}
              onPress={() => setPeriod(item.value)}
            >
              <Text
                style={[
                  styles.periodText,
                  item.value === period ? styles.periodTextActive : null
                ]}
              >
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <ScrollView style={styles.chartScroll} contentContainerStyle={styles.chartContent}>
        <NativeKLineChart bars={payload?.bars ?? []} />
      </ScrollView>
    </View>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

function formatPercent(value: number | null): string {
  if (value === null) return "--";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function formatNumber(value: number | null): string {
  if (value === null) return "--";
  return value.toLocaleString(undefined, {
    maximumFractionDigits: value >= 100 ? 2 : 8
  });
}

function formatMarketMetric(value: number | null): string {
  if (value === null) return "--";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000_000) return `${(value / 1_000_000_000_000).toFixed(1)}T`;
  if (abs >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#ffffff"
  },
  topBar: {
    minHeight: 96,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingTop: 34,
    backgroundColor: "#ffffff"
  },
  iconButton: {
    width: 30,
    height: 36,
    justifyContent: "center"
  },
  iconText: {
    color: "#111827",
    fontSize: 38,
    lineHeight: 38
  },
  titleBlock: {
    flex: 1,
    minWidth: 0,
    paddingLeft: 8
  },
  symbolRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  symbol: {
    color: "#030712",
    fontSize: 22,
    fontWeight: "900"
  },
  badge: {
    overflow: "hidden",
    borderRadius: 5,
    backgroundColor: "#f1f1f1",
    color: "#111827",
    fontSize: 13,
    fontWeight: "700",
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  name: {
    marginTop: 5,
    color: "#737373",
    fontSize: 13
  },
  refreshButton: {
    minHeight: 32,
    justifyContent: "center",
    borderRadius: 7,
    backgroundColor: "#f5f5f5",
    paddingHorizontal: 10
  },
  refreshText: {
    color: "#111111",
    fontSize: 13,
    fontWeight: "800"
  },
  statsRow: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "center",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#eeeeee",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee",
    paddingHorizontal: 14
  },
  metric: {
    flex: 1
  },
  metricLabel: {
    color: "#737373",
    fontSize: 12
  },
  metricValue: {
    marginTop: 3,
    color: "#111111",
    fontSize: 15,
    fontWeight: "700"
  },
  error: {
    paddingHorizontal: 18,
    color: "#dc2626",
    fontSize: 13
  },
  periods: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    gap: 22,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#f1f1f1",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee",
    paddingHorizontal: 14
  },
  periodPressable: {
    minHeight: 36,
    justifyContent: "center"
  },
  periodText: {
    color: "#777777",
    fontSize: 15,
    fontWeight: "800"
  },
  periodTextActive: {
    color: "#111111"
  },
  chartScroll: {
    flex: 1,
    backgroundColor: "#ffffff"
  },
  chartContent: {
    paddingHorizontal: 0,
    paddingBottom: 18
  }
});
