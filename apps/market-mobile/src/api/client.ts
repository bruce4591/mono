import { API_BASE_URL } from "../config";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = "API_ERROR"
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as { error?: unknown };
    const message = typeof payload.error === "string" ? payload.error : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return response.json() as Promise<T>;
}
