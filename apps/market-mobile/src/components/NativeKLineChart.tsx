import React from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import type { MobileBar } from "../api/types";
import { EmptyState } from "./EmptyState";

const CHART_HEIGHT = 180;
const CANDLE_WIDTH = 10;
const CANDLE_GAP = 6;

export function NativeKLineChart({ bars }: { bars: MobileBar[] }) {
  const visibleBars = bars.slice(-80).filter((bar) => {
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
  const range = Math.max(high - low, 0.000001);

  return (
    <View style={styles.root}>
      <View style={styles.axisRow}>
        <Text style={styles.axisText}>{formatPrice(high)}</Text>
        <Text style={styles.axisText}>{formatPrice(low)}</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={styles.chart}>
          {visibleBars.map((bar, index) => {
            const rising = bar.close >= bar.open;
            const wickTop = ((high - bar.high) / range) * CHART_HEIGHT;
            const wickHeight = Math.max(((bar.high - bar.low) / range) * CHART_HEIGHT, 1);
            const bodyTop = ((high - Math.max(bar.open, bar.close)) / range) * CHART_HEIGHT;
            const bodyHeight = Math.max(
              (Math.abs(bar.close - bar.open) / range) * CHART_HEIGHT,
              2
            );
            return (
              <View key={`${bar.time}:${index}`} style={styles.slot}>
                <View
                  style={[
                    styles.wick,
                    {
                      top: wickTop,
                      height: wickHeight,
                      backgroundColor: rising ? "#f87171" : "#34d399"
                    }
                  ]}
                />
                <View
                  style={[
                    styles.body,
                    {
                      top: bodyTop,
                      height: bodyHeight,
                      backgroundColor: rising ? "#f87171" : "#34d399"
                    }
                  ]}
                />
              </View>
            );
          })}
        </View>
      </ScrollView>
    </View>
  );
}

function formatPrice(value: number): string {
  return value.toLocaleString(undefined, {
    maximumFractionDigits: value >= 100 ? 2 : 4
  });
}

const styles = StyleSheet.create({
  root: {
    minHeight: CHART_HEIGHT + 28,
    marginTop: 18
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
  chart: {
    position: "relative",
    height: CHART_HEIGHT,
    flexDirection: "row",
    alignItems: "stretch",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: "#173438"
  },
  slot: {
    position: "relative",
    width: CANDLE_WIDTH,
    height: CHART_HEIGHT,
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
    borderRadius: 2
  }
});
