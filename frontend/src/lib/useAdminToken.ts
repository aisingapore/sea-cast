// Admin-token lifecycle for the History page (the gated /sessions* endpoints).
//
// Mirrors useRoomToken's localStorage-with-expiry pattern, but the token comes
// from POSTing a username/password to /admin/login rather than from joining a
// room. To use another login method (e.g. OAuth/OIDC), replace `login`; the
// api.ts helpers that attach the token and the HistoryView gate stay as they are.

import { useCallback, useState } from "react";

import { adminLogin } from "./api";

// Treat a token as expired this far before its real expiry, so it can't lapse
// mid-request.
const TOKEN_SLACK_MS = 60_000;
const STORAGE_KEY = "sea-cast-admin";

type StoredAdmin = { token: string; expires_at: number };

function loadStored(): StoredAdmin | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredAdmin) : null;
  } catch {
    return null; // private mode / corrupt entry — behave like logged out
  }
}

function freshToken(): string | null {
  const stored = loadStored();
  return stored && stored.expires_at > Date.now() + TOKEN_SLACK_MS
    ? stored.token
    : null;
}

export function useAdminToken(): {
  isAuthed: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  getToken: () => string | null;
} {
  const [token, setToken] = useState<string | null>(() => freshToken());

  const login = useCallback(async (username: string, password: string) => {
    // Propagates ApiError (401 bad creds, 503 unconfigured) to the caller.
    const issued = await adminLogin(username, password);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(issued));
    } catch {
      // Storage unavailable: the session still works until reload.
    }
    setToken(issued.token);
  }, []);

  const logout = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore — clearing state below is what matters
    }
    setToken(null);
  }, []);

  // Reads through storage so an expiry that elapsed while the page was open is
  // caught (returns null → the page shows the login form again).
  const getToken = useCallback(() => freshToken(), []);

  return { isAuthed: token !== null, login, logout, getToken };
}
