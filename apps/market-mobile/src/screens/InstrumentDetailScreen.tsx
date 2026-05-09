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
  { label: "1小时", value: "1h" },
  { label: "4小时", value: "4h" },
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
        <View style={styles.topActions}>
          <Text style={styles.actionIcon}>☆</Text>
          <Text style={styles.actionIcon}>铃</Text>
        </View>
      </View>
      <View style={styles.sectionTabs}>
        {["价格", "信息", "交易数据", "代币检测", "广场", "交易-X"].map((item, index) => (
          <Text key={item} style={[styles.sectionTab, index === 0 ? styles.sectionTabActive : null]}>
            {item}
          </Text>
        ))}
      </View>
      <View style={styles.statsRow}>
        <Metric label="标记价格" value={formatNumber(payload?.snapshot.last_price ?? null)} />
        <Metric label="涨跌幅" value={formatPercent(payload?.snapshot.change_pct ?? null)} />
        <Metric label="成交额" value={formatMarketMetric(payload?.snapshot.turnover ?? null)} />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.periods}>
        {PERIOD_TABS.map((item) => {
          const available = payload?.periods.includes(item.value) || item.value === "1d";
          return (
            <Pressable
              key={item.value}
              disabled={!available}
              style={styles.periodPressable}
              onPress={() => setPeriod(item.value)}
            >
              <Text
                style={[
                  styles.periodText,
                  item.value === period ? styles.periodTextActive : null,
                  !available ? styles.periodTextDisabled : null
                ]}
              >
                {item.label}
              </Text>
            </Pressable>
          );
        })}
        <Text style={styles.periodMore}>更多⌄</Text>
        <Text style={styles.toolIcon}>⌗</Text>
        <Text style={styles.toolIcon}>◫</Text>
      </View>
      <ScrollView style={styles.chartScroll} contentContainerStyle={styles.chartContent}>
        <NativeKLineChart bars={payload?.bars ?? []} />
      </ScrollView>
      <View style={styles.bottomBar}>
        <Pressable style={styles.bottomTool}>
          <Text style={styles.bottomIcon}>•••</Text>
          <Text style={styles.bottomLabel}>更多</Text>
        </Pressable>
        <Pressable style={styles.bottomTool}>
          <Text style={styles.bottomIcon}>▦</Text>
          <Text style={styles.bottomLabel}>工具</Text>
        </Pressable>
        <Pressable style={styles.bottomTool}>
          <Text style={styles.bottomIcon}>◎</Text>
          <Text style={styles.bottomLabel}>现货</Text>
        </Pressable>
        <Pressable style={styles.tradeButton} onPress={() => void loadDetail(true)}>
          <Text style={styles.tradeButtonText}>{loading ? "刷新中" : "交易"}</Text>
        </Pressable>
      </View>
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
    minHeight: 112,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 18,
    paddingTop: 42,
    backgroundColor: "#ffffff"
  },
  iconButton: {
    width: 36,
    height: 36,
    justifyContent: "center"
  },
  iconText: {
    color: "#111827",
    fontSize: 42,
    lineHeight: 42
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
    fontSize: 25,
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
    fontSize: 15
  },
  topActions: {
    flexDirection: "row",
    gap: 22
  },
  actionIcon: {
    color: "#030712",
    fontSize: 28,
    fontWeight: "800"
  },
  sectionTabs: {
    minHeight: 46,
    flexDirection: "row",
    alignItems: "flex-end",
    gap: 26,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee",
    paddingHorizontal: 18
  },
  sectionTab: {
    color: "#777777",
    fontSize: 18,
    fontWeight: "800",
    paddingBottom: 12
  },
  sectionTabActive: {
    color: "#111111",
    borderBottomWidth: 4,
    borderBottomColor: "#fcd535"
  },
  statsRow: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 18
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
    minHeight: 52,
    flexDirection: "row",
    alignItems: "center",
    gap: 20,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#f1f1f1",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee",
    paddingHorizontal: 18
  },
  periodPressable: {
    minHeight: 36,
    justifyContent: "center"
  },
  periodText: {
    color: "#777777",
    fontSize: 16,
    fontWeight: "800"
  },
  periodTextActive: {
    color: "#111111"
  },
  periodTextDisabled: {
    color: "#c7c7c7"
  },
  periodMore: {
    color: "#777777",
    fontSize: 16,
    fontWeight: "800"
  },
  toolIcon: {
    color: "#111111",
    fontSize: 20,
    fontWeight: "900"
  },
  chartScroll: {
    flex: 1,
    backgroundColor: "#ffffff"
  },
  chartContent: {
    paddingHorizontal: 0,
    paddingBottom: 18
  },
  bottomBar: {
    minHeight: 82,
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#eeeeee",
    paddingHorizontal: 18,
    paddingBottom: 10,
    backgroundColor: "#ffffff"
  },
  bottomTool: {
    width: 42,
    alignItems: "center"
  },
  bottomIcon: {
    color: "#111111",
    fontSize: 20,
    fontWeight: "900"
  },
  bottomLabel: {
    marginTop: 3,
    color: "#111111",
    fontSize: 13,
    fontWeight: "700"
  },
  tradeButton: {
    flex: 1,
    minHeight: 54,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 12,
    backgroundColor: "#fcd535"
  },
  tradeButtonText: {
    color: "#111111",
    fontSize: 20,
    fontWeight: "900"
  }
});
