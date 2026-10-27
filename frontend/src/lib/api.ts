// Backend base-URL resolution + typed fetch helpers for the rooms API.

import type {
  AdminToken,
  CreateRoomRequest,
  CreateRoomResponse,
  JoinResponse,
  RoomInfo,
  SessionDetail,
  SessionSummary,
} from "./captionTypes";

// The backend's base URL. VITE_SERVER_URL wins (`||`, so an empty value falls
// through). Otherwise: in local dev (Vite on :5173), the page's hostname on
// :8080, so the app also works when opened via the laptop's LAN IP; in a
// production build, the page's own origin (the backend serves the built app;
// see STATIC_DIR).
export function serverUrl(): string {
  return (
    import.meta.env.VITE_SERVER_URL ||
    (import.meta.env.DEV
      ? `http://${window.location.hostname}:8080`
      : window.location.origin)
  );
}

// ws(s):// base derived from the HTTP server URL.
export function wsBase(): string {
  return serverUrl().replace(/^http/, "ws");
}

export function captionsSocketUrl(roomId: string, lang: string, token: string): string {
  const url = new URL(`${wsBase()}/rooms/${encodeURIComponent(roomId)}/captions`);
  url.searchParams.set("lang", lang);
  url.searchParams.set("token", token);
  return url.toString();
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${serverUrl()}${path}`, init);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text().catch(() => res.statusText));
  }
  return (await res.json()) as T;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

export function createRoom(body: CreateRoomRequest): Promise<CreateRoomResponse> {
  return requestJson<CreateRoomResponse>("/rooms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getRoom(roomId: string): Promise<RoomInfo> {
  return requestJson<RoomInfo>(`/rooms/${encodeURIComponent(roomId)}`);
}

export function joinRoom(roomId: string): Promise<JoinResponse> {
  return requestJson<JoinResponse>(`/rooms/${encodeURIComponent(roomId)}/join`, {
    method: "POST",
  });
}

// ---- Admin session-history API (gated by the admin token) ----

// Trade the shared admin credential for a short-lived token. ApiError(401) on
// bad credentials; ApiError(503) when the deploy has no admin creds configured.
export function adminLogin(username: string, password: string): Promise<AdminToken> {
  return requestJson<AdminToken>("/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
}

// The /sessions* endpoints require the admin token; an expired/invalid token
// surfaces as ApiError(401) so the caller can drop it and show the login form.
export function getSessions(token: string): Promise<SessionSummary[]> {
  return requestJson<SessionSummary[]>("/sessions", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

export function getSession(token: string, id: string): Promise<SessionDetail> {
  return requestJson<SessionDetail>(`/sessions/${encodeURIComponent(id)}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Delete one or more sessions. Unlike download (a plain <a href> that must pass
// the token in the query string), this is a real fetch, so it carries the token
// in the Authorization header like getSessions/getSession — a lapsed token
// surfaces as ApiError(401) and funnels through onAuthError.
export function deleteSessions(
  token: string,
  ids: string[],
): Promise<{ deleted: string[] }> {
  const qs = new URLSearchParams({ ids: ids.join(",") });
  return requestJson<{ deleted: string[] }>(`/sessions?${qs}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
}

// Download + audio are plain-GET resources loaded by <a href> / <audio src>,
// which can't send an Authorization header — so the token rides as ?token=
// (the require_admin dependency accepts either). Omitting ids downloads all.
export function sessionsDownloadUrl(token: string, ids?: string[]): string {
  const url = new URL(`${serverUrl()}/sessions/download`);
  if (ids && ids.length > 0) url.searchParams.set("ids", ids.join(","));
  url.searchParams.set("token", token);
  return url.toString();
}

export function sessionAudioUrl(token: string, id: string): string {
  const url = new URL(`${serverUrl()}/sessions/${encodeURIComponent(id)}/audio`);
  url.searchParams.set("token", token);
  return url.toString();
}
