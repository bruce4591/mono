import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute, type InstrumentRoute } from "../app/navigation";
import { fetchMobileInstrumentDetail } from "../api/market";
import type { MobileBar, MobileInstrumentDetailPayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { NativeKLineChart } from "../components/NativeKLineChart";
import { theme } from "../theme";

const PERIOD_TABS = [
  { label: "1分", value: "1m" },
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
  const displayTime = displayBar ? formatSelectedTime(displayBar.time, period) : "--";
  const priceLabel = isCryptoContract(route.market, payload?.instrument.asset_class ?? null)
    ? "标记"
    : "最新";

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
        <SummaryCell label={priceLabel} value={formatNumber(payload?.snapshot.last_price ?? null)} />
        <SummaryCell label="成交额" value={formatMarketMetric(payload?.snapshot.turnover ?? null)} />
        <SummaryCell label="开" value={formatCompactPrice(displayBar?.open ?? null)} />
        <SummaryCell label="高" value={formatCompactPrice(displayBar?.high ?? null)} />
        <SummaryCell label="涨跌幅" value={formatPercent(payload?.snapshot.change_pct ?? null)} />
        <View style={styles.summaryCell} />
        <SummaryCell label="低" value={formatCompactPrice(displayBar?.low ?? null)} />
        <SummaryCell label="收" value={formatCompactPrice(displayBar?.close ?? null)} />
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.periodBar}>
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
        <Text style={styles.selectedTime} numberOfLines={1}>
          {displayTime}
        </Text>
      </View>
      <View style={styles.chartArea}>
        <NativeKLineChart
          bars={payload?.bars ?? []}
          alertMarkers={payload?.alert_markers ?? []}
          period={period}
          resetKey={`${route.market}:${route.symbol}:${period}`}
          onSelectedBarChange={setSelectedBar}
          onReachStart={loadMoreHistory}
        />
      </View>
    </View>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.summaryCell}>
      <Text style={styles.summaryLabel} numberOfLines={1}>
        {label}
      </Text>
      <Text
        style={styles.summaryValue}
        numberOfLines={1}
        adjustsFontSizeToFit
        minimumFontScale={0.78}
      >
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

function formatCompactPrice(value: number | null): string {
  if (value === null) return "--";
  const abs = Math.abs(value);
  if (abs > 0 && abs < 1) return value.toFixed(6);
  if (abs >= 1000) return value.toFixed(2);
  return value.toFixed(4);
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

function isCryptoContract(market: string, assetClass: string | null): boolean {
  return market.toUpperCase() === "CRYPTO" || assetClass?.toLowerCase().includes("crypto") === true;
}

function formatSelectedTime(value: string, period: string): string {
  const parsed = parseTimeParts(value);
  if (parsed === null) return value;
  if (period === "1d") {
    return `${parsed.year}-${parsed.month}-${parsed.day}`;
  }
  return `${parsed.month}-${parsed.day} ${parsed.hour}:${parsed.minute}`;
}

function parseTimeParts(value: string): {
  year: string;
  month: string;
  day: string;
  hour: string;
  minute: string;
} | null {
  const normalized = value.includes("T") ? value : `${value}T00:00:00Z`;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return null;
  return {
    year: String(date.getUTCFullYear()),
    month: String(date.getUTCMonth() + 1).padStart(2, "0"),
    day: String(date.getUTCDate()).padStart(2, "0"),
    hour: String(date.getUTCHours()).padStart(2, "0"),
    minute: String(date.getUTCMinutes()).padStart(2, "0")
  };
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: theme.colors.background
  },
  topBar: {
    minHeight: 78,
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 14,
    paddingTop: 24,
    backgroundColor: theme.colors.background
  },
  iconButton: {
    width: 28,
    height: 32,
    justifyContent: "center"
  },
  iconText: {
    color: theme.colors.text,
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
    color: theme.colors.textStrong,
    fontSize: 20,
    fontWeight: "900"
  },
  badge: {
    overflow: "hidden",
    borderRadius: 5,
    backgroundColor: theme.colors.surface,
    color: theme.colors.text,
    fontSize: 13,
    fontWeight: "700",
    paddingHorizontal: 7,
    paddingVertical: 3
  },
  name: {
    marginTop: 3,
    color: theme.colors.textMuted,
    fontSize: 12
  },
  refreshButton: {
    minHeight: 32,
    justifyContent: "center",
    borderRadius: 7,
    backgroundColor: theme.colors.accent,
    paddingHorizontal: 10
  },
  refreshText: {
    color: theme.colors.text,
    fontSize: 13,
    fontWeight: "800"
  },
  marketPanel: {
    minHeight: 62,
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingHorizontal: 12,
    paddingVertical: 5,
    rowGap: 3
  },
  summaryCell: {
    width: "25%",
    minHeight: 24,
    paddingRight: 6,
    justifyContent: "center"
  },
  summaryLabel: {
    color: theme.colors.textMuted,
    fontSize: 9,
    fontWeight: "700"
  },
  summaryValue: {
    color: theme.colors.text,
    fontSize: 12,
    lineHeight: 15,
    fontWeight: "800"
  },
  error: {
    paddingHorizontal: 18,
    color: theme.colors.danger,
    fontSize: 13
  },
  periodBar: {
    minHeight: 36,
    maxHeight: 36,
    flexDirection: "row",
    alignItems: "center",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingRight: 12
  },
  periodScroll: {
    flex: 1,
    minHeight: 36,
    maxHeight: 36
  },
  periods: {
    minHeight: 36,
    flexDirection: "row",
    alignItems: "center",
    gap: 20,
    paddingHorizontal: 14,
    paddingRight: 24
  },
  selectedTime: {
    color: theme.colors.textMuted,
    fontSize: 11,
    fontWeight: "800"
  },
  periodPressable: {
    minHeight: 32,
    justifyContent: "center"
  },
  periodText: {
    color: theme.colors.textMuted,
    fontSize: 13,
    fontWeight: "800"
  },
  periodTextActive: {
    color: theme.colors.text
  },
  chartArea: {
    flex: 1,
    backgroundColor: theme.colors.background
  }
});
