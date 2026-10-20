// REST helpers (same-origin, cookie session).

export async function apiGet<T = any>(path: string, params?: { [k: string]: string | number | undefined }): Promise<T> {
  const url = new URL(path, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  const r = await fetch(url.toString(), { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } });
  if (r.status === 401) {
    location.href = `/auth/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error("auth required");
  }
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return (await r.json()) as T;
}

export async function apiSend<T = any>(method: string, path: string, body?: unknown, params?: { [k: string]: string | number | undefined }): Promise<T> {
  const url = new URL(path, location.origin);
  if (params) for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
  const r = await fetch(url.toString(), {
    method,
    credentials: "same-origin",
    cache: "no-store",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (r.status === 401) {
    location.href = `/auth/login?next=${encodeURIComponent(location.pathname + location.search)}`;
    throw new Error("auth required");
  }
  if (!r.ok) {
    let detail = `${r.status}`;
    try {
      detail = (await r.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const text = await r.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const apiPost = <T = any>(path: string, body?: unknown, params?: { [k: string]: string | number | undefined }) => apiSend<T>("POST", path, body, params);
export const apiPut = <T = any>(path: string, body?: unknown, params?: { [k: string]: string | number | undefined }) => apiSend<T>("PUT", path, body, params);
export const apiDelete = <T = any>(path: string) => apiSend<T>("DELETE", path);
