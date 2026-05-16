import React, { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { AppRoute, BoardGroup, HomeRoute } from "../app/navigation";
import { fetchMobileHome } from "../api/market";
import type { MobileHomePayload } from "../api/types";
import { getCached } from "../cache/queryCache";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { MarketList } from "../components/MarketList";
import { theme } from "../theme";

export function HomeScreen({
  route,
  navigate,
  onRouteStateChange
}: {
  route: HomeRoute;
  navigate: (route: AppRoute) => void;
  onRouteStateChange?: (route: HomeRoute) => void;
}) {
  const [payload, setPayload] = useState<MobileHomePayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<BoardGroup>(route.group ?? "tradfi");
  const [selectedBoardKey, setSelectedBoardKey] = useState<string | null>(
    route.boardKey ?? null
  );

  async function loadHome(force = false) {
    setLoading(true);
    setError(null);
    try {
      const nextPayload = await getCached({
        key: "mobile-home",
        ttlMs: 60_000,
        load: fetchMobileHome,
        force
      });
      setPayload(nextPayload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "首页加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadHome();
  }, []);

  useEffect(() => {
    if (route.group && route.group !== selectedGroup) {
      setSelectedGroup(route.group);
      setSelectedBoardKey(null);
    }
  }, [route.group, selectedGroup]);

  const visibleBoards = useMemo(
    () =>
      payload?.boards.filter(
        (board) => board.items.length > 0 && boardGroupForKey(board.key) === selectedGroup
      ) ?? [],
    [payload, selectedGroup]
  );

  useEffect(() => {
    const firstBoard = visibleBoards[0];
    const selectedStillVisible = visibleBoards.some((board) => board.key === selectedBoardKey);
    if (firstBoard && (!selectedBoardKey || !selectedStillVisible)) {
      setSelectedBoardKey(firstBoard.key);
    }
  }, [visibleBoards, selectedBoardKey]);

  const selectedBoard =
    visibleBoards.find((board) => board.key === selectedBoardKey) ?? visibleBoards[0] ?? null;

  useEffect(() => {
    onRouteStateChange?.({
      name: "home",
      group: selectedGroup,
      boardKey: selectedBoard?.key ?? selectedBoardKey
    });
  }, [onRouteStateChange, selectedBoard?.key, selectedBoardKey, selectedGroup]);

  if (loading && !payload) {
    return <LoadingState label="加载排行榜" />;
  }

  if (error && !payload) {
    return <ErrorState message={error} onRetry={() => void loadHome(true)} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Market</Text>
          <Text style={styles.subtitle}>
            {payload ? `${selectedGroup === "tradfi" ? "Traditional Finance" : "Crypto"} · ${visibleBoards.length} boards` : "榜单 --"}
          </Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable onPress={() => navigate({ name: "alertEvents" })}>
            <Text style={styles.link}>Alerts</Text>
          </Pressable>
          <Pressable onPress={() => navigate({ name: "alertRules" })}>
            <Text style={styles.link}>Rules</Text>
          </Pressable>
          <Pressable onPress={() => navigate({ name: "strategies" })}>
            <Text style={styles.link}>Strategies</Text>
          </Pressable>
          <Pressable onPress={() => navigate({ name: "settings" })}>
            <Text style={styles.link}>Settings</Text>
          </Pressable>
          <Pressable style={styles.headerRefreshButton} onPress={() => void loadHome(true)}>
            <Text style={styles.headerRefreshText}>{loading ? "刷新中" : "刷新"}</Text>
          </Pressable>
        </View>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <View style={styles.groupTabs}>
        {(["tradfi", "crypto"] as BoardGroup[]).map((group) => {
          const active = group === selectedGroup;
          return (
            <Pressable
              key={group}
              style={[styles.groupTab, active ? styles.groupTabActive : null]}
              onPress={() => {
                setSelectedGroup(group);
                setSelectedBoardKey(null);
              }}
            >
              <Text style={[styles.groupTabText, active ? styles.groupTabTextActive : null]}>
                {group === "tradfi" ? "Traditional Finance" : "Crypto"}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <View style={styles.toolbar}>
        <ScrollView
          horizontal
          style={styles.tabScroll}
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.tabs}
        >
          {visibleBoards.map((board) => {
            const active = board.key === selectedBoard?.key;
            return (
              <Pressable
                key={board.key}
                style={[styles.tab, active ? styles.tabActive : null]}
                onPress={() => setSelectedBoardKey(board.key)}
              >
                <Text style={[styles.tabText, active ? styles.tabTextActive : null]}>
                  {board.title}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
      <ScrollView style={styles.scroll} contentContainerStyle={styles.scrollContent}>
        <MarketList boards={selectedBoard ? [selectedBoard] : []} navigate={navigate} />
      </ScrollView>
    </View>
  );
}

function boardGroupForKey(key: string): BoardGroup {
  return key.startsWith("CRYPTO") ? "crypto" : "tradfi";
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: theme.colors.background
  },
  header: {
    paddingHorizontal: 16,
    paddingTop: 48,
    paddingBottom: 14,
    gap: 10
  },
  title: {
    color: theme.colors.textStrong,
    fontSize: 24,
    fontWeight: "900"
  },
  subtitle: {
    marginTop: 8,
    color: theme.colors.textMuted,
    fontSize: 15
  },
  headerActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 14
  },
  error: {
    paddingHorizontal: 16,
    paddingBottom: 8,
    color: theme.colors.danger,
    fontSize: 13
  },
  link: {
    color: theme.colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  headerRefreshButton: {
    minHeight: 28,
    justifyContent: "center",
    borderRadius: 7,
    backgroundColor: theme.colors.accent,
    paddingHorizontal: 12
  },
  headerRefreshText: {
    color: theme.colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  groupTabs: {
    flexDirection: "row",
    gap: 10,
    marginHorizontal: 16,
    marginBottom: 12
  },
  groupTab: {
    flex: 1,
    minHeight: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: 10
  },
  groupTabActive: {
    backgroundColor: theme.colors.accent
  },
  groupTabText: {
    color: theme.colors.textMuted,
    fontSize: 14,
    fontWeight: "900"
  },
  groupTabTextActive: {
    color: theme.colors.text
  },
  toolbar: {
    minHeight: 42,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginHorizontal: 16,
    marginBottom: 8
  },
  tabScroll: {
    flex: 1
  },
  tabs: {
    gap: 8,
    paddingRight: 4
  },
  tab: {
    minHeight: 34,
    justifyContent: "center",
    borderRadius: 8,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: 12
  },
  tabActive: {
    backgroundColor: theme.colors.accent
  },
  tabText: {
    color: theme.colors.textMuted,
    fontSize: 14,
    fontWeight: "800"
  },
  tabTextActive: {
    color: theme.colors.text
  },
  scroll: {
    flex: 1
  },
  scrollContent: {
    paddingHorizontal: 16
  }
});
