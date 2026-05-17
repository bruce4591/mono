import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";
import type { MobileBoardItem } from "../api/types";
import { theme } from "../theme";
import { DataTimeBadge } from "./DataTimeBadge";
import { PriceChange } from "./PriceChange";

export function MarketListItem({
  item,
  navigate
}: {
  item: MobileBoardItem;
  navigate: (route: AppRoute) => void;
}) {
  return (
    <Pressable
      style={styles.row}
      onPress={() => {
        navigate({ name: "instrument", market: item.market, symbol: item.symbol });
      }}
    >
      <View style={styles.left}>
        <Text style={styles.symbol}>{item.symbol}</Text>
        <Text style={styles.name} numberOfLines={1}>
          {item.name}
        </Text>
        <DataTimeBadge value={item.data_time} />
      </View>
      <View style={styles.right}>
        <Text style={styles.price}>{formatNumber(item.last_price, item.unit)}</Text>
        <PriceChange value={item.change_pct} />
        <Text style={styles.metric}>{item.metric_label ?? formatMarketMetric(item.turnover ?? item.volume)}</Text>
      </View>
    </Pressable>
  );
}

function formatNumber(value: number | null, unit?: string | null): string {
  if (value === null) return "--";
  const maximumFractionDigits = unit === "%" ? 3 : value >= 100 ? 2 : 4;
  const formatted = value.toLocaleString(undefined, {
    maximumFractionDigits
  });
  return unit ? `${formatted}${unit}` : formatted;
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
  row: {
    minHeight: 78,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingVertical: 10
  },
  left: {
    flex: 1,
    minWidth: 0,
    paddingRight: 12
  },
  symbol: {
    color: theme.colors.textStrong,
    fontSize: 16,
    fontWeight: "800"
  },
  name: {
    marginTop: 3,
    color: theme.colors.textMuted,
    fontSize: 13
  },
  right: {
    alignItems: "flex-end",
    gap: 3
  },
  price: {
    color: theme.colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  metric: {
    color: theme.colors.textSubtle,
    fontSize: 12
  }
});
