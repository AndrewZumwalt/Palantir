const API_BASE = "/api";
const AUTH_TOKEN_KEY = "palantir_auth_token";

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

// Global listener for auth failures so an AuthGate component can react.
// We deliberately do NOT auto-reload on 401 — that caused an infinite loop
// whenever the token was missing or stale.
type AuthFailHandler = () => void;
const authFailHandlers = new Set<AuthFailHandler>();
export function onAuthFail(handler: AuthFailHandler): () => void {
  authFailHandlers.add(handler);
  return () => authFailHandlers.delete(handler);
}

export function getAuthToken(): string | null {
  return localStorage.getItem(AUTH_TOKEN_KEY);
}

export function setAuthToken(token: string): void {
  localStorage.setItem(AUTH_TOKEN_KEY, token);
}

export function clearAuthToken(): void {
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    if (response.status === 401) {
      // Notify subscribers (AuthGate) that the token is missing / invalid.
      // Do NOT clear + reload — that loops forever with no login UI.
      authFailHandlers.forEach((h) => h());
    }
    // Surface the FastAPI `detail` field when present so callers see
    // the actual error ("Face detector not available", "Multiple faces
    // detected", etc.) rather than a bare HTTP status.
    let detail: string | null = null;
    try {
      const body = await response.clone().json();
      if (body && typeof body.detail === "string") {
        detail = body.detail;
      } else if (typeof body === "string") {
        detail = body;
      }
    } catch {
      // not JSON — try plain text
      try {
        const text = await response.text();
        if (text) detail = text;
      } catch {
        // give up; we'll fall back to the status line
      }
    }
    const message = detail
      ? `${response.status} ${response.statusText}: ${detail}`
      : `API error: ${response.status} ${response.statusText}`;
    throw new ApiError(response.status, message);
  }

  return response.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
