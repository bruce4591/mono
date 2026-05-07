import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute, type InstrumentRoute } from "../app/navigation";
import { fetchMobileInstrumentDetail } from "../api/market";
import type { MobileInstrumentDetailPayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { DataTimeBadge } from "../components/DataTimeBadge";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { NativeKLineChart } from "../components/NativeKLineChart";
import { PriceChange } from "../components/PriceChange";

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
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <View style={styles.topBar}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回</Text>
        </Pressable>
        <Pressable onPress={() => void loadDetail(true)}>
          <Text style={styles.refresh}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <Text style={styles.title}>
        {payload?.instrument.name || `${route.market} ${route.symbol}`}
      </Text>
      <Text style={styles.subtitle}>
        {route.market} {route.symbol}
      </Text>
      <View style={styles.priceRow}>
        <Text style={styles.price}>{formatNumber(payload?.snapshot.last_price ?? null)}</Text>
        <PriceChange value={payload?.snapshot.change_pct ?? null} />
      </View>
      <View style={styles.metaGrid}>
        <Metric label="成交额" value={formatMarketMetric(payload?.snapshot.turnover ?? null)} />
        <Metric label="成交量" value={formatMarketMetric(payload?.snapshot.volume ?? null)} />
        <Metric label="来源" value={payload?.snapshot.source || "--"} />
      </View>
      <DataTimeBadge value={payload?.snapshot.data_time ?? null} />
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.periods}>
        {(payload?.periods.length ? payload.periods : [period]).map((item) => (
          <Pressable
            key={item}
            style={[styles.periodButton, item === period ? styles.periodButtonActive : null]}
            onPress={() => setPeriod(item)}
          >
            <Text style={[styles.periodText, item === period ? styles.periodTextActive : null]}>
              {item}
            </Text>
          </Pressable>
        ))}
      </View>
      <NativeKLineChart bars={payload?.bars ?? []} />
    </ScrollView>
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

function formatNumber(value: number | null): string {
  if (value === null) return "--";
  return value.toLocaleString(undefined, {
    maximumFractionDigits: value >= 100 ? 2 : 4
  });
}

function formatMarketMetric(value: number | null): string {
  if (value === null) return "--";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 48,
    paddingBottom: 36
  },
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  back: {
    color: "#5eead4",
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  subtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 15
  },
  priceRow: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginTop: 20
  },
  price: {
    color: "#f8fafc",
    fontSize: 34,
    fontWeight: "800"
  },
  metaGrid: {
    flexDirection: "row",
    gap: 10,
    marginTop: 18,
    marginBottom: 10
  },
  metric: {
    flex: 1,
    minHeight: 54,
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: "#0d2023",
    paddingHorizontal: 10
  },
  metricLabel: {
    color: "#8ea4aa",
    fontSize: 12
  },
  metricValue: {
    marginTop: 5,
    color: "#d6e2e4",
    fontSize: 14,
    fontWeight: "700"
  },
  error: {
    marginTop: 12,
    color: "#fca5a5",
    fontSize: 13
  },
  refresh: {
    color: "#5eead4",
    fontSize: 16
  },
  periods: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 22
  },
  periodButton: {
    minWidth: 48,
    minHeight: 34,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: "#102528",
    paddingHorizontal: 12
  },
  periodButtonActive: {
    backgroundColor: "#5eead4"
  },
  periodText: {
    color: "#b9cdd2",
    fontSize: 14,
    fontWeight: "700"
  },
  periodTextActive: {
    color: "#071113"
  }
});
