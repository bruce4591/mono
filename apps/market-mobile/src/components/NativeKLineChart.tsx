import React from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import type { MobileBar } from "../api/types";
import { EmptyState } from "./EmptyState";

const PRICE_CHART_HEIGHT = 210;
const VOLUME_CHART_HEIGHT = 56;
const CANDLE_WIDTH = 8;
const CANDLE_GAP = 5;
const MAX_VISIBLE_BARS = 90;
const UP_COLOR = "#0ecb81";
const DOWN_COLOR = "#f6465d";
const MA7_COLOR = "#fcd535";
const MA25_COLOR = "#c084fc";

export function NativeKLineChart({ bars }: { bars: MobileBar[] }) {
  const visibleBars = bars.slice(-MAX_VISIBLE_BARS).filter((bar) => {
    return (
      Number.isFinite(bar.open) &&
      Number.isFinite(bar.high) &&
      Number.isFinite(bar.low) &&
      Number.isFinite(bar.close)
    );
  });
  if (visibleBars.length === 0) {
    return <EmptyState title="暂无 K 线" message="当前周期没有可展示的数据" />;
  }

  const high = Math.max(...visibleBars.map((bar) => bar.high));
  const low = Math.min(...visibleBars.map((bar) => bar.low));
  const pricePadding = Math.max((high - low) * 0.08, Math.abs(high) * 0.001, 0.000001);
  const paddedHigh = high + pricePadding;
  const paddedLow = low - pricePadding;
  const range = Math.max(paddedHigh - paddedLow, 0.000001);
  const maxVolume = Math.max(...visibleBars.map((bar) => bar.volume ?? 0), 0.000001);
  const latest = visibleBars[visibleBars.length - 1];
  const previous = visibleBars.length > 1 ? visibleBars[visibleBars.length - 2] : null;
  const latestChange = previous ? latest.close - previous.close : latest.close - latest.open;
  const latestChangePct = previous && previous.close !== 0 ? (latestChange / previous.close) * 100 : null;
  const ma7 = movingAverage(visibleBars, 7);
  const ma25 = movingAverage(visibleBars, 25);
  const latestMa7 = ma7[ma7.length - 1];
  const latestMa25 = ma25[ma25.length - 1];

  return (
    <View style={styles.root}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>K线</Text>
        <Text style={styles.latestText}>
          {latest.time}
        </Text>
      </View>
      <View style={styles.ohlcRow}>
        <Info label="开" value={formatPrice(latest.open)} />
        <Info label="高" value={formatPrice(latest.high)} />
        <Info label="低" value={formatPrice(latest.low)} />
        <Info label="收" value={formatPrice(latest.close)} />
        <Info
          label="涨跌"
          value={latestChangePct === null ? "--" : `${latestChangePct >= 0 ? "+" : ""}${latestChangePct.toFixed(2)}%`}
          tone={latestChange >= 0 ? "up" : "down"}
        />
      </View>
      <View style={styles.legendRow}>
        <Text style={[styles.legendText, { color: MA7_COLOR }]}>
          MA7 {latestMa7 ? formatPrice(latestMa7) : "--"}
        </Text>
        <Text style={[styles.legendText, { color: MA25_COLOR }]}>
          MA25 {latestMa25 ? formatPrice(latestMa25) : "--"}
        </Text>
        <Text style={styles.legendText}>Vol {formatMetric(latest.volume ?? null)}</Text>
      </View>
      <View style={styles.axisRow}>
        <Text style={styles.axisText}>{formatPrice(paddedHigh)}</Text>
        <Text style={styles.axisText}>{formatPrice((paddedHigh + paddedLow) / 2)}</Text>
        <Text style={styles.axisText}>{formatPrice(paddedLow)}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View>
          <View style={styles.priceChart}>
            <View style={[styles.gridLine, { top: 0 }]} />
            <View style={[styles.gridLine, { top: PRICE_CHART_HEIGHT / 2 }]} />
            <View style={[styles.gridLine, { bottom: 0 }]} />
            {visibleBars.map((bar, index) => {
              const rising = bar.close >= bar.open;
              const color = rising ? UP_COLOR : DOWN_COLOR;
              const wickTop = yForPrice(bar.high, paddedHigh, range);
              const wickBottom = yForPrice(bar.low, paddedHigh, range);
              const bodyTop = yForPrice(Math.max(bar.open, bar.close), paddedHigh, range);
              const bodyBottom = yForPrice(Math.min(bar.open, bar.close), paddedHigh, range);
              const bodyHeight = Math.max(bodyBottom - bodyTop, 2);
              const ma7Value = ma7[index];
              const ma25Value = ma25[index];
              return (
                <View key={`${bar.time}:${index}`} style={styles.slot}>
                  {ma7Value ? (
                    <View
                      style={[
                        styles.maDot,
                        {
                          top: clamp(yForPrice(ma7Value, paddedHigh, range), 0, PRICE_CHART_HEIGHT - 2),
                          backgroundColor: MA7_COLOR
                        }
                      ]}
                    />
                  ) : null}
                  {ma25Value ? (
                    <View
                      style={[
                        styles.maDot,
                        {
                          top: clamp(yForPrice(ma25Value, paddedHigh, range), 0, PRICE_CHART_HEIGHT - 2),
                          backgroundColor: MA25_COLOR
                        }
                      ]}
                    />
                  ) : null}
                  <View
                    style={[
                      styles.wick,
                      {
                        top: wickTop,
                        height: Math.max(wickBottom - wickTop, 1),
                        backgroundColor: color
                      }
                    ]}
                  />
                  <View
                    style={[
                      styles.body,
                      {
                        top: bodyTop,
                        height: bodyHeight,
                        borderColor: color,
                        backgroundColor: rising ? color : "#0b0f14"
                      }
                    ]}
                  />
                </View>
              );
            })}
          </View>
          <View style={styles.volumeChart}>
            {visibleBars.map((bar, index) => {
              const rising = bar.close >= bar.open;
              const volumeHeight = Math.max(((bar.volume ?? 0) / maxVolume) * VOLUME_CHART_HEIGHT, 1);
              return (
                <View key={`volume:${bar.time}:${index}`} style={styles.volumeSlot}>
                  <View
                    style={[
                      styles.volumeBar,
                      {
                        height: volumeHeight,
                        backgroundColor: rising ? "rgba(14, 203, 129, 0.42)" : "rgba(246, 70, 93, 0.42)"
                      }
                    ]}
                  />
                </View>
              );
            })}
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

function Info({
  label,
  value,
  tone
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
}) {
  return (
    <Text style={styles.infoText}>
      {label} <Text style={tone === "up" ? styles.upText : tone === "down" ? styles.downText : styles.infoValue}>{value}</Text>
    </Text>
  );
}

function yForPrice(price: number, high: number, range: number): number {
  return ((high - price) / range) * PRICE_CHART_HEIGHT;
}

function movingAverage(bars: MobileBar[], period: number): Array<number | null> {
  let sum = 0;
  return bars.map((bar, index) => {
    sum += bar.close;
    if (index >= period) {
      sum -= bars[index - period].close;
    }
    if (index < period - 1) {
      return null;
    }
    return sum / period;
  });
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: value >= 100 ? 2 : 4
  });
}

function formatMetric(value: number | null): string {
  if (value === null) return "--";
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

const styles = StyleSheet.create({
  root: {
    minHeight: PRICE_CHART_HEIGHT + VOLUME_CHART_HEIGHT + 116,
    marginTop: 18
  },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "baseline",
    marginBottom: 8
  },
  title: {
    color: "#f8fafc",
    fontSize: 16,
    fontWeight: "800"
  },
  latestText: {
    color: "#8ea4aa",
    fontSize: 12
  },
  ohlcRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 8
  },
  infoText: {
    color: "#8ea4aa",
    fontSize: 12
  },
  infoValue: {
    color: "#d6e2e4",
    fontWeight: "700"
  },
  upText: {
    color: UP_COLOR,
    fontWeight: "800"
  },
  downText: {
    color: DOWN_COLOR,
    fontWeight: "800"
  },
  legendRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 12,
    marginBottom: 8
  },
  legendText: {
    color: "#8ea4aa",
    fontSize: 12,
    fontWeight: "700"
  },
  axisRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 20
  },
  axisText: {
    color: "#8ea4aa",
    fontSize: 12
  },
  priceChart: {
    position: "relative",
    height: PRICE_CHART_HEIGHT,
    flexDirection: "row",
    alignItems: "stretch",
    backgroundColor: "#0b0f14",
    borderColor: "#173438"
  },
  gridLine: {
    position: "absolute",
    left: 0,
    right: 0,
    height: StyleSheet.hairlineWidth,
    backgroundColor: "#173438"
  },
  slot: {
    position: "relative",
    width: CANDLE_WIDTH,
    height: PRICE_CHART_HEIGHT,
    marginRight: CANDLE_GAP
  },
  wick: {
    position: "absolute",
    left: CANDLE_WIDTH / 2 - 1,
    width: 2
  },
  body: {
    position: "absolute",
    left: 0,
    width: CANDLE_WIDTH,
    borderWidth: 1,
    borderRadius: 2
  },
  maDot: {
    position: "absolute",
    left: CANDLE_WIDTH / 2 - 1,
    zIndex: 2,
    width: 2,
    height: 2,
    borderRadius: 1
  },
  volumeChart: {
    height: VOLUME_CHART_HEIGHT,
    flexDirection: "row",
    alignItems: "flex-end",
    marginTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#173438"
  },
  volumeSlot: {
    position: "relative",
    width: CANDLE_WIDTH,
    height: VOLUME_CHART_HEIGHT,
    marginRight: CANDLE_GAP
  },
  volumeBar: {
    position: "absolute",
    bottom: 0,
    left: 0,
    width: CANDLE_WIDTH,
    borderRadius: 1
  }
});
