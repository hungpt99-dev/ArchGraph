export type Json = Record<string, any>;

const TOKEN_KEY = "archgraph.token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.body && !(options.body instanceof FormData) ? { "content-type": "application/json" } : {}),
    ...(token ? { authorization: `Bearer ${token}` } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };
  const res = await fetch(`/api${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* keep default */ }
    if (res.status === 401) setToken(null);
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T = any>(path: string) => api<T>(path);
export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = <T = any>(path: string) => api<T>(path, { method: "DELETE" });

export const fmt = {
  number: (n: number | null | undefined) => (n ?? 0).toLocaleString(),
  date: (value?: string | null) => (value ? new Date(value).toLocaleString() : "—"),
  ago: (value?: string | null) => {
    if (!value) return "—";
    const diff = Date.now() - new Date(value).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "just now";
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    return `${Math.floor(hrs / 24)}d ago`;
  },
  commit: (sha?: string | null) => (sha ? sha.slice(0, 7) : "—"),
};
