import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { fetchMobileAlertEventsPayload } from "../api/alerts";
import type { MobileAlertEvent } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";
import { theme } from "../theme";

export function AlertEventsScreen({
  navigate,
  pushToken,
  readEventIds,
  onReadEvent
}: {
  navigate: (route: AppRoute) => void;
  pushToken: string | null;
  readEventIds: Set<number>;
  onReadEvent: (eventId: number) => void;
}) {
  const [events, setEvents] = useState<MobileAlertEvent[]>([]);
  const [loading, setLoading] = useState(Boolean(pushToken));
  const [error, setError] = useState<string | null>(null);

  async function loadEvents() {
    if (!pushToken) return;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchMobileAlertEventsPayload({ pushToken, afterId: 0 });
      setEvents(payload.events);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提醒事件加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadEvents();
  }, [pushToken]);

  if (!pushToken) {
    return (
      <View style={styles.root}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <EmptyState title="等待设备注册" message="推送 token 注册完成后会显示提醒事件" />
      </View>
    );
  }

  if (loading && events.length === 0) {
    return <LoadingState label="加载提醒事件" />;
  }

  if (error && events.length === 0) {
    return <ErrorState message={error} onRetry={() => void loadEvents()} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <Pressable onPress={() => void loadEvents()}>
          <Text style={styles.refresh}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <Text style={styles.title}>提醒事件</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <ScrollView style={styles.list}>
        {events.length === 0 ? <EmptyState title="暂无提醒事件" /> : null}
        {events.map((event) => {
          const isNew = !readEventIds.has(event.mobile_alert_event_id);
          const period = typeof event.data?.period === "string" ? event.data.period : undefined;
          return (
            <Pressable
              key={event.mobile_alert_event_id}
              style={styles.item}
              onPress={() => {
                onReadEvent(event.mobile_alert_event_id);
                navigate({ name: "instrument", market: event.market, symbol: event.symbol, period });
              }}
            >
              <View style={styles.itemHeader}>
                <Text style={styles.itemTitle}>{event.title}</Text>
                {isNew ? <Text style={styles.newBadge}>NEW</Text> : null}
              </View>
              <Text style={styles.itemBody}>{event.body}</Text>
              <Text style={styles.itemMeta}>
                {event.market} {event.symbol}
                {period ? ` · ${period}` : ""} · {event.delivery_status}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    paddingHorizontal: 16,
    paddingTop: 48,
    backgroundColor: theme.colors.background
  },
  back: {
    color: theme.colors.text,
    fontSize: 15
  },
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  refresh: {
    color: theme.colors.text,
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: theme.colors.textStrong,
    fontSize: 24,
    fontWeight: "700"
  },
  error: {
    marginTop: 10,
    color: theme.colors.danger,
    fontSize: 13
  },
  list: {
    marginTop: 18
  },
  item: {
    minHeight: 84,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingVertical: 12
  },
  itemHeader: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8
  },
  itemTitle: {
    flex: 1,
    color: theme.colors.textStrong,
    fontSize: 16,
    fontWeight: "700"
  },
  newBadge: {
    borderRadius: 3,
    backgroundColor: theme.colors.accent,
    paddingHorizontal: 5,
    paddingVertical: 2,
    color: theme.colors.textStrong,
    fontSize: 10,
    fontWeight: "800",
    overflow: "hidden"
  },
  itemBody: {
    marginTop: 5,
    color: theme.colors.text,
    fontSize: 14,
    lineHeight: 20
  },
  itemMeta: {
    marginTop: 7,
    color: theme.colors.textMuted,
    fontSize: 12
  }
});
