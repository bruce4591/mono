import { fetchJson } from "./client";
import type { MobileHomePayload, MobileInstrumentDetailPayload } from "./types";

export function fetchMobileHome(): Promise<MobileHomePayload> {
  return fetchJson<MobileHomePayload>("/api/mobile/home");
}

export function fetchMobileInstrumentDetail({
  market,
  symbol,
  period = "1d"
}: {
  market: string;
  symbol: string;
  period?: string;
}): Promise<MobileInstrumentDetailPayload> {
  const params = new URLSearchParams({ market, symbol, period });
  return fetchJson<MobileInstrumentDetailPayload>(`/api/mobile/instrument-detail?${params}`);
}
