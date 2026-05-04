import { NativeModules, Platform } from "react-native";

type MarketGetuiModule = {
  initialize: () => Promise<boolean>;
  getClientId: () => Promise<string | null>;
  isOnline: () => Promise<boolean>;
};

const nativeModule = NativeModules.MarketGetui as MarketGetuiModule | undefined;

export async function initializeGetuiPush(): Promise<void> {
  if (Platform.OS !== "android" || !nativeModule) {
    return;
  }
  await nativeModule.initialize();
}

export async function getGetuiClientId(): Promise<string | null> {
  if (Platform.OS !== "android" || !nativeModule) {
    return null;
  }
  const cid = await nativeModule.getClientId();
  return cid && cid.length > 0 ? cid : null;
}

export async function waitForGetuiClientId(
  timeoutMs = 15000,
  intervalMs = 1000
): Promise<string | null> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() <= deadline) {
    const cid = await getGetuiClientId();
    if (cid) {
      return cid;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  return null;
}
