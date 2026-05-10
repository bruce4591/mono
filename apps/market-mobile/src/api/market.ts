import { fetchJson } from "./client";
import type { MobileHomePayload, MobileInstrumentDetailPayload } from "./types";

export function fetchMobileHome(): Promise<MobileHomePayload> {
  return fetchJson<MobileHomePayload>("/api/mobile/home");
}

export function fetchMobileInstrumentDetail({
  market,
  symbol,
  period = "1d",
  dailyLimit,
  intradayLimit
}: {
  market: string;
  symbol: string;
  period?: string;
  dailyLimit?: number;
  intradayLimit?: number;
}): Promise<MobileInstrumentDetailPayload> {
  const params = new URLSearchParams({ market, symbol, period });
  if (dailyLimit !== undefined) {
    params.set("daily_limit", String(dailyLimit));
  }
  if (intradayLimit !== undefined) {
    params.set("intraday_limit", String(intradayLimit));
  }
  return fetchJson<MobileInstrumentDetailPayload>(`/api/mobile/instrument-detail?${params}`);
}
