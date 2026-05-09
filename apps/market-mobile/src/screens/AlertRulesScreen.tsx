import React, { useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { homeRoute, type AppRoute } from "../app/navigation";
import { fetchMobileAlertRulesPayload, patchMobileAlertRulePayload } from "../api/alerts";
import type { MobileAlertRule } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { ErrorState } from "../components/ErrorState";
import { LoadingState } from "../components/LoadingState";

export function AlertRulesScreen({
  navigate,
  pushToken
}: {
  navigate: (route: AppRoute) => void;
  pushToken: string | null;
}) {
  const [rules, setRules] = useState<MobileAlertRule[]>([]);
  const [loading, setLoading] = useState(Boolean(pushToken));
  const [error, setError] = useState<string | null>(null);

  async function loadRules() {
    if (!pushToken) return;
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchMobileAlertRulesPayload({ pushToken });
      setRules(payload.rules);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提醒规则加载失败");
    } finally {
      setLoading(false);
    }
  }

  async function toggleRule(rule: MobileAlertRule) {
    try {
      const nextRule = await patchMobileAlertRulePayload({
        ruleId: rule.mobile_alert_rule_id,
        enabled: !rule.enabled
      });
      setRules((current) =>
        current.map((item) =>
          item.mobile_alert_rule_id === nextRule.mobile_alert_rule_id ? nextRule : item
        )
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "规则更新失败");
    }
  }

  useEffect(() => {
    void loadRules();
  }, [pushToken]);

  if (!pushToken) {
    return (
      <View style={styles.root}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <EmptyState title="等待设备注册" message="推送 token 注册完成后会显示提醒规则" />
      </View>
    );
  }

  if (loading && rules.length === 0) {
    return <LoadingState label="加载提醒规则" />;
  }

  if (error && rules.length === 0) {
    return <ErrorState message={error} onRetry={() => void loadRules()} />;
  }

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <Pressable onPress={() => navigate(homeRoute)}>
          <Text style={styles.back}>返回行情</Text>
        </Pressable>
        <Pressable onPress={() => void loadRules()}>
          <Text style={styles.refresh}>{loading ? "刷新中" : "刷新"}</Text>
        </Pressable>
      </View>
      <Text style={styles.title}>提醒规则</Text>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      <ScrollView style={styles.list}>
        {rules.length === 0 ? <EmptyState title="暂无提醒规则" /> : null}
        {rules.map((rule) => (
          <View key={rule.mobile_alert_rule_id} style={styles.item}>
            <Text style={styles.itemTitle}>
              {rule.market} {rule.symbol}
            </Text>
            <Text style={styles.itemBody}>
              {rule.condition_type} {rule.threshold}
            </Text>
            <Text style={styles.itemMeta}>冷却 {rule.cooldown_seconds ?? "--"} 秒</Text>
            <Pressable style={styles.toggle} onPress={() => void toggleRule(rule)}>
              <Text style={styles.toggleText}>{rule.enabled ? "停用" : "启用"}</Text>
            </Pressable>
          </View>
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
    minHeight: 100,
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
    fontSize: 14
  },
  itemMeta: {
    marginTop: 7,
    color: "#9fb2b7",
    fontSize: 12
  },
  toggle: {
    alignSelf: "flex-start",
    marginTop: 10,
    borderRadius: 8,
    backgroundColor: "#123638",
    paddingHorizontal: 14,
    paddingVertical: 8
  },
  toggleText: {
    color: "#dff8f5",
    fontSize: 14,
    fontWeight: "600"
  }
});
