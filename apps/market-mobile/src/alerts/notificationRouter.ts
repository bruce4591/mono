import type { AppRoute } from "../app/navigation";

export function routeFromNotificationData(data: Record<string, unknown>): AppRoute {
  const market = typeof data.market === "string" ? data.market : null;
  const symbol = typeof data.symbol === "string" ? data.symbol : null;
  if (market && symbol) {
    return { name: "instrument", market, symbol };
  }
  return { name: "alertEvents" };
}
