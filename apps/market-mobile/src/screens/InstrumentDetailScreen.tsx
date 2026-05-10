import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute, type InstrumentRoute } from "../app/navigation";
import { fetchMobileInstrumentDetail } from "../api/market";
import type { MobileBar, MobileInstrumentDetailPayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { NativeKLineChart } from "../components/NativeKLineChart";

const PERIOD_TABS = [
  { label: "分时", value: "1m" },
  { label: "5分", value: "5m" },
  { label: "15分", value: "15m" },
  { label: "8小时", value: "8h" },
  { label: "1天", value: "1d" }
];

const INITIAL_HISTORY_LIMIT = 120;
const HISTORY_LIMIT_STEP = 120;
const MAX_HISTORY_LIMIT = 500;

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
  const [historyLimit, setHistoryLimit] = useState(INITIAL_HISTORY_LIMIT);
  const [selectedBar, setSelectedBar] = useState<MobileBar | null>(null);
  const historyRequestInFlightRef = useRef(false);

  async function loadDetail(force = false) {
    setLoading(true);
    setError(null);
    try {
      const nextPayload = await getCached({
        key: `instrument:${route.market}:${route.symbol}:${period}:${historyLimit}`,
        ttlMs: 60_000,
        load: () =>
          fetchMobileInstrumentDetail({
            market: route.market,
            symbol: route.symbol,
            period,
            dailyLimit: period === "1d" ? historyLimit : undefined,
            intradayLimit: period === "1d" ? undefined : historyLimit
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
  }, [route.market, route.symbol, period, historyLimit]);

  useEffect(() => {
    setHistoryLimit(INITIAL_HISTORY_LIMIT);
    setSelectedBar(null);
  }, [route.market, route.symbol]);

  useEffect(() => {
    if (!loading) {
      historyRequestInFlightRef.current = false;
    }
  }, [loading, historyLimit]);

  function selectPeriod(nextPeriod: string) {
    if (nextPeriod === period) return;
    setHistoryLimit(INITIAL_HISTORY_LIMIT);
    setSelectedBar(null);
    setPeriod(nextPeriod);
  }

  function loadMoreHistory() {
    if (
      historyRequestInFlightRef.current ||
      loading ||
      (payload?.bars.length ?? 0) < historyLimit ||
      historyLimit >= MAX_HISTORY_LIMIT
    ) {
      return;
    }
    historyRequestInFlightRef.current = true;
    setHistoryLimit((value) => Math.min(value + HISTORY_LIMIT_STEP, MAX_HISTORY_LIMIT));
  }

  if (loading && !payload) {
    return <LoadingState label="加载详情" />;
  }

  if (error && !payload) {
    return <ErrorState message={error} onRetry={() => void loadDetail(true)} />;
  }

  const latestBar = payload?.bars[payload.bars.length - 1] ?? null;
  const displayBar = selectedBar ?? latestBar;

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
      <View style={styles.marketPanel}>
        <View style={styles.snapshotGrid}>
          <Metric label="标记价格" value={formatNumber(payload?.snapshot.last_price ?? null)} />
          <Metric label="涨跌幅" value={formatPercent(payload?.snapshot.change_pct ?? null)} />
          <Metric label="成交额" value={formatMarketMetric(payload?.snapshot.turnover ?? null)} />
        </View>
        <View style={styles.ohlcGrid}>
          <Metric compact label="开" value={formatNumber(displayBar?.open ?? null)} />
          <Metric compact label="高" value={formatNumber(displayBar?.high ?? null)} />
          <Metric compact label="低" value={formatNumber(displayBar?.low ?? null)} />
          <Metric compact label="收" value={formatNumber(displayBar?.close ?? null)} />
        </View>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={styles.periodScroll}
        contentContainerStyle={styles.periods}
      >
        {PERIOD_TABS.filter((item) => item.value === period || payload?.periods.includes(item.value)).map((item) => {
          return (
            <Pressable
              key={item.value}
              style={styles.periodPressable}
              onPress={() => selectPeriod(item.value)}
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
      </ScrollView>
      <View style={styles.chartArea}>
        <NativeKLineChart
          bars={payload?.bars ?? []}
          period={period}
          resetKey={`${route.market}:${route.symbol}:${period}`}
          onSelectedBarChange={setSelectedBar}
          onReachStart={loadMoreHistory}
        />
      </View>
    </View>
  );
}

function Metric({ label, value, compact = false }: { label: string; value: string; compact?: boolean }) {
  return (
    <View style={compact ? styles.metricCompact : styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={compact ? styles.metricValueCompact : styles.metricValue} numberOfLines={1}>
        {value}
      </Text>
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
    minHeight: 78,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingTop: 24,
    backgroundColor: "#ffffff"
  },
  iconButton: {
    width: 28,
    height: 32,
    justifyContent: "center"
  },
  iconText: {
    color: "#111827",
    fontSize: 34,
    lineHeight: 34
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
    fontSize: 20,
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
    marginTop: 3,
    color: "#737373",
    fontSize: 12
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
  marketPanel: {
    minHeight: 58,
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#eeeeee",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee",
    paddingHorizontal: 14,
    paddingVertical: 7,
    gap: 12
  },
  snapshotGrid: {
    flex: 1.25,
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  ohlcGrid: {
    flex: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    rowGap: 4,
    columnGap: 8
  },
  metric: {
    flex: 1
  },
  metricCompact: {
    width: "46%"
  },
  metricLabel: {
    color: "#737373",
    fontSize: 11
  },
  metricValue: {
    marginTop: 2,
    color: "#111111",
    fontSize: 13,
    fontWeight: "700"
  },
  metricValueCompact: {
    marginTop: 2,
    color: "#111111",
    fontSize: 11,
    fontWeight: "800"
  },
  error: {
    paddingHorizontal: 18,
    color: "#dc2626",
    fontSize: 13
  },
  periodScroll: {
    minHeight: 36,
    maxHeight: 36,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#f1f1f1",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#eeeeee"
  },
  periods: {
    minHeight: 36,
    flexDirection: "row",
    alignItems: "center",
    gap: 20,
    paddingHorizontal: 14,
    paddingRight: 24
  },
  periodPressable: {
    minHeight: 32,
    justifyContent: "center"
  },
  periodText: {
    color: "#777777",
    fontSize: 14,
    fontWeight: "800"
  },
  periodTextActive: {
    color: "#111111"
  },
  chartArea: {
    flex: 1,
    backgroundColor: "#ffffff"
  }
});
