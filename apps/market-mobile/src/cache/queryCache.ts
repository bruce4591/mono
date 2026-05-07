type CacheEntry<T> = {
  expiresAt: number;
  value: T;
};

const cache = new Map<string, CacheEntry<unknown>>();

export async function getCached<T>({
  key,
  ttlMs,
  load,
  force = false
}: {
  key: string;
  ttlMs: number;
  load: () => Promise<T>;
  force?: boolean;
}): Promise<T> {
  const now = Date.now();
  const existing = cache.get(key) as CacheEntry<T> | undefined;
  if (!force && existing && existing.expiresAt > now) {
    return existing.value;
  }
  const value = await load();
  cache.set(key, { value, expiresAt: now + ttlMs });
  return value;
}
