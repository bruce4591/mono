import React from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { AppRoute } from "../app/navigation";

type MenuItem = {
  title: string;
  subtitle: string;
  route: AppRoute;
};

const MENU_ITEMS: MenuItem[] = [
  {
    title: "行情排行榜",
    subtitle: "ETF、港股、美股、加密货币榜单",
    route: { name: "home" }
  },
  {
    title: "提醒事件",
    subtitle: "查看已经触发并投递的消息",
    route: { name: "alertEvents" }
  },
  {
    title: "提醒规则",
    subtitle: "管理价格、成交额和自定义指标提醒",
    route: { name: "alertRules" }
  },
  {
    title: "推送与后台",
    subtitle: "查看 Push、个推 CID、SSE 和后台设置",
    route: { name: "settings" }
  }
];

export function MenuScreen({ navigate }: { navigate: (route: AppRoute) => void }) {
  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.eyebrow}>Market</Text>
      <Text style={styles.title}>菜单</Text>
      <Text style={styles.subtitle}>行情、提醒和推送调试入口</Text>
      <View style={styles.grid}>
        {MENU_ITEMS.map((item) => (
          <Pressable key={item.title} style={styles.card} onPress={() => navigate(item.route)}>
            <Text style={styles.cardTitle}>{item.title}</Text>
            <Text style={styles.cardSubtitle}>{item.subtitle}</Text>
            <Text style={styles.cardAction}>进入</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.quickSection}>
        <Text style={styles.sectionTitle}>快速查看</Text>
        <Pressable
          style={styles.quickButton}
          onPress={() => navigate({ name: "instrument", market: "HK", symbol: "09988" })}
        >
          <Text style={styles.quickTitle}>HK 09988</Text>
          <Text style={styles.quickSubtitle}>阿里巴巴详情和 K 线</Text>
        </Pressable>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: "#071113"
  },
  content: {
    paddingHorizontal: 16,
    paddingTop: 52,
    paddingBottom: 36
  },
  eyebrow: {
    color: "#5eead4",
    fontSize: 14,
    fontWeight: "800"
  },
  title: {
    marginTop: 8,
    color: "#f8fafc",
    fontSize: 30,
    fontWeight: "800"
  },
  subtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 15,
    lineHeight: 22
  },
  grid: {
    gap: 12,
    marginTop: 24
  },
  card: {
    minHeight: 104,
    borderRadius: 8,
    backgroundColor: "#0d2023",
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: "#1e454a",
    padding: 16
  },
  cardTitle: {
    color: "#f8fafc",
    fontSize: 18,
    fontWeight: "800"
  },
  cardSubtitle: {
    marginTop: 8,
    color: "#9fb2b7",
    fontSize: 14,
    lineHeight: 20
  },
  cardAction: {
    marginTop: 14,
    color: "#5eead4",
    fontSize: 14,
    fontWeight: "800"
  },
  quickSection: {
    marginTop: 28
  },
  sectionTitle: {
    color: "#d6e2e4",
    fontSize: 15,
    fontWeight: "800"
  },
  quickButton: {
    marginTop: 10,
    borderRadius: 8,
    backgroundColor: "#123638",
    padding: 14
  },
  quickTitle: {
    color: "#f8fafc",
    fontSize: 16,
    fontWeight: "800"
  },
  quickSubtitle: {
    marginTop: 6,
    color: "#9fb2b7",
    fontSize: 13
  }
});
