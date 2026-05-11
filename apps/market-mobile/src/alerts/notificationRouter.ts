import type { AppRoute } from "../app/navigation";

export function routeFromNotificationData(data: Record<string, unknown>): AppRoute {
  const market = typeof data.market === "string" ? data.market : null;
  const symbol = typeof data.symbol === "string" ? data.symbol : null;
  const period = typeof data.period === "string" ? data.period : undefined;
  if (market && symbol) {
    return { name: "instrument", market, symbol, period };
  }
  return { name: "alertEvents" };
}
