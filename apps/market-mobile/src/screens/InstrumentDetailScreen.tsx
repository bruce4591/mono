import React, { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute, type InstrumentRoute } from "../app/navigation";
import { fetchMobileInstrumentDetail } from "../api/market";
import type { MobileInstrumentDetailPayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";

export function InstrumentDetailScreen({
  route,
  navigate
}: {
  route: InstrumentRoute;
  navigate: (route: AppRoute) => void;
}) {
  const [period] = useState("1d");
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
      <Pressable onPress={() => navigate(homeRoute)}>
        <Text style={styles.back}>返回</Text>
      </Pressable>
      <Text style={styles.title}>
        {route.market} {route.symbol}
      </Text>
      <Text style={styles.subtitle}>原生详情页占位</Text>
      <Text style={styles.meta}>
        {payload?.snapshot.data_time ? `数据时间 ${payload.snapshot.data_time}` : "数据时间 --"}
      </Text>
      <Text style={styles.meta}>
        {payload ? `K 线 ${payload.bars.length} 条` : "K 线 --"}
      </Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <Pressable onPress={() => void loadDetail(true)}>
        <Text style={styles.refresh}>刷新</Text>
      </Pressable>
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
  meta: {
    marginTop: 14,
    color: "#d6e2e4",
    fontSize: 14,
    lineHeight: 20
  },
  error: {
    marginTop: 12,
    color: "#fca5a5",
    fontSize: 13
  },
  refresh: {
    marginTop: 20,
    color: "#5eead4",
    fontSize: 16
  }
});
