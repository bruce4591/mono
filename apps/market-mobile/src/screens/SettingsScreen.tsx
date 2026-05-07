import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { API_BASE_URL } from "../config";
import { ONEPLUS_13T_BACKGROUND_CHECKLIST } from "../oneplusGuidance";

export function SettingsScreen({
  navigate,
  pushToken,
  getuiClientId,
  latestSeenAlertEventId
}: {
  navigate: (route: AppRoute) => void;
  pushToken: string | null;
  getuiClientId: string | null;
  latestSeenAlertEventId: number;
}) {
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Pressable onPress={() => navigate(homeRoute)}>
        <Text style={styles.back}>返回</Text>
      </Pressable>
      <Text style={styles.title}>设置</Text>
      <Section title="连接">
        <InfoRow label="API" value={API_BASE_URL} />
        <InfoRow label="Expo Push" value={pushToken ? "已注册" : "未注册"} />
        <InfoRow label="个推 CID" value={getuiClientId ? "已获取" : "未获取"} />
        <InfoRow label="SSE" value={pushToken ? "前台可连接" : "等待 token"} />
        <InfoRow label="最新事件" value={String(latestSeenAlertEventId)} />
      </Section>
      <Section title="后台运行">
        {ONEPLUS_13T_BACKGROUND_CHECKLIST.map((item) => (
          <Text key={item} style={styles.checkItem}>
            {item}
          </Text>
        ))}
      </Section>
    </ScrollView>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.label}>{label}</Text>
      <Text style={styles.value}>{value}</Text>
    </View>
  );
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
  section: {
    marginTop: 24,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#245057",
    paddingTop: 14
  },
  sectionTitle: {
    marginBottom: 10,
    color: "#f8fafc",
    fontSize: 17,
    fontWeight: "700"
  },
  row: {
    minHeight: 38,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 14
  },
  label: {
    color: "#9fb2b7",
    fontSize: 13
  },
  value: {
    flex: 1,
    color: "#d6e2e4",
    fontSize: 14,
    lineHeight: 20,
    textAlign: "right"
  },
  checkItem: {
    minHeight: 30,
    color: "#d6e2e4",
    fontSize: 14,
    lineHeight: 22
  }
});
