import React from "react";
import { StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";
import type { MobileBoard } from "../api/types";
import { EmptyState } from "./EmptyState";
import { MarketListItem } from "./MarketListItem";

export function MarketList({
  boards,
  navigate
}: {
  boards: MobileBoard[];
  navigate: (route: AppRoute) => void;
}) {
  const visibleBoards = boards.filter((board) => board.items.length > 0);
  if (visibleBoards.length === 0) {
    return <EmptyState title="暂无榜单数据" message="稍后刷新或检查后端行情同步状态" />;
  }

  return (
    <View style={styles.root}>
      {visibleBoards.map((board) => (
        <View key={board.key} style={styles.section}>
          <View style={styles.header}>
            <Text style={styles.title}>{board.title}</Text>
            <Text style={styles.count}>{board.items.length}</Text>
          </View>
          {board.items.slice(0, 12).map((item) => (
            <MarketListItem key={`${board.key}:${item.market}:${item.symbol}`} item={item} navigate={navigate} />
          ))}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    gap: 20,
    paddingBottom: 32
  },
  section: {
    width: "100%"
  },
  header: {
    minHeight: 32,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#245057"
  },
  title: {
    color: "#f8fafc",
    fontSize: 17,
    fontWeight: "700"
  },
  count: {
    color: "#8ea4aa",
    fontSize: 13
  }
});
