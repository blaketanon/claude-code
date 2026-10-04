const TOKEN_KEY = "alphaforge.token";

export function getToken(): string | null {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(t: string | null) {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}

export class ApiError extends Error { status: number; constructor(s: number, m: string) { super(m); this.status = s; } }

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as any) };
  const tok = getToken();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  if (opts.body && !(opts.body instanceof FormData) && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { setToken(null); window.dispatchEvent(new Event("alphaforge:logout")); }
  if (!r.ok) throw new ApiError(r.status, (await r.text()) || r.statusText);
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : (r.text() as any);
}

export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, body?: any) => api<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T = any>(p: string, body?: any) => api<T>(p, { method: "PUT", body: JSON.stringify(body) });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

export async function login(username: string, password: string) {
  const form = new URLSearchParams({ username, password });
  const r = await fetch("/api/auth/token", { method: "POST", body: form, headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  if (!r.ok) throw new ApiError(r.status, "Invalid credentials");
  const j = await r.json();
  setToken(j.access_token);
  return j;
}

export const fmtPct = (v: number | null | undefined, d = 2) => v == null ? "–" : `${(v * 100).toFixed(d)}%`;
export const fmtNum = (v: number | null | undefined, d = 2) => v == null ? "–" : Number(v).toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmtMoney = (v: number | null | undefined) => v == null ? "–" : Number(v).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const fmtTs = (s: string | null | undefined) => s ? new Date(s).toLocaleString() : "–";
