import { fetchJson } from "./client";
import type { MobileStrategiesPayload } from "./types";

export function fetchMobileStrategiesPayload(): Promise<MobileStrategiesPayload> {
  return fetchJson<MobileStrategiesPayload>("/api/mobile/strategies");
}
