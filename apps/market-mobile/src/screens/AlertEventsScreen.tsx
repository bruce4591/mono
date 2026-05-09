import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { fetchMobileAlertEventsPayload } from "../api/alerts";
import type { MobileAlertEvent } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";

export function AlertEventsScreen({
  navigate,
  pushToken
}: {
  navigate: (route: AppRoute) => void;
  pushToken: string | null;
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
        {events.map((event) => (
          <Pressable
            key={event.mobile_alert_event_id}
            style={styles.item}
            onPress={() => navigate({ name: "instrument", market: event.market, symbol: event.symbol })}
          >
            <Text style={styles.itemTitle}>{event.title}</Text>
            <Text style={styles.itemBody}>{event.body}</Text>
            <Text style={styles.itemMeta}>
              {event.market} {event.symbol} · {event.delivery_status}
            </Text>
          </Pressable>
        ))}
      </ScrollView>
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
  topBar: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center"
  },
  refresh: {
    color: "#5eead4",
    fontSize: 15
  },
  title: {
    marginTop: 20,
    color: "#f8fafc",
    fontSize: 24,
    fontWeight: "700"
  },
  error: {
    marginTop: 10,
    color: "#fca5a5",
    fontSize: 13
  },
  list: {
    marginTop: 18
  },
  item: {
    minHeight: 84,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#173438",
    paddingVertical: 12
  },
  itemTitle: {
    color: "#f8fafc",
    fontSize: 16,
    fontWeight: "700"
  },
  itemBody: {
    marginTop: 5,
    color: "#b9cdd2",
    fontSize: 14,
    lineHeight: 20
  },
  itemMeta: {
    marginTop: 7,
    color: "#9fb2b7",
    fontSize: 12
  }
});
