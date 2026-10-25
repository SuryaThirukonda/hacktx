// REST helpers. An optional access token (OVERWATCH_TOKEN on the server) is read from ?token= once and kept locally.

const TOKEN_KEY = "overwatch.token";

function readToken(): string | null {
  try {
    const fragment = new URLSearchParams(window.location.hash.slice(1));
    const sharedToken = fragment.get("token");
    if (sharedToken) {
      fragment.delete("token");
      const rest = fragment.toString();
      history.replaceState(null, "", window.location.pathname + window.location.search + (rest ? "#" + rest : ""));
    }
    const fromUrl = sharedToken ?? new URLSearchParams(window.location.search).get("token");
    if (fromUrl) localStorage.setItem(TOKEN_KEY, fromUrl);
    return fromUrl ?? localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export const token = readToken();

export function withToken(path: string): string {
  if (!token) return path;
  return path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown, raw?: Blob | string): Promise<T> {
  const res = await fetch(withToken(path), {
    method,
    headers: raw !== undefined ? { "Content-Type": "application/octet-stream" } : body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: raw !== undefined ? raw : body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.error?.message ?? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j));
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b: unknown = {}) => request<T>("POST", p, b),
  /** Send a file or text as the raw request body (scene files, transform.json). */
  postRaw: <T>(p: string, raw: Blob | string) => request<T>("POST", p, undefined, raw),
  del: <T>(p: string) => request<T>("DELETE", p),
};

export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return withToken(`${proto}//${window.location.host}${path}`);
}
