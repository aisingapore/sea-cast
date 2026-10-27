// Join-token lifecycle for one room, used by the audience view, the stage
// monitor and the dashboard's translation column.
//
// The join result (token + languages) is cached in localStorage per room, so a
// revisit or reload within the token's lifetime (6 h by default) skips the join
// round-trip and — on the audience side — the language picker. `getToken` is
// what the captions socket calls: it serves the cached token while fresh,
// re-joins when stale or when the server closed with 4001 (forceRefresh), and
// lets an ApiError(404) propagate — the socket treats that as "room gone"
// (terminal).

import { useCallback, useEffect, useState } from "react";

import { ApiError, joinRoom } from "./api";
import type { GetToken } from "./captionsSocket";
import type { JoinResponse } from "./captionTypes";

// Re-join this long before the token's actual expiry, so a token can't lapse
// between the freshness check and the WebSocket handshake.
const TOKEN_SLACK_MS = 60_000;

type StoredJoin = {
  token: string;
  expires_at: number; // epoch ms
  languages: string[];
  source_language: string;
  include_source: boolean;
  lang?: string; // audience's last-picked caption language
};

const storageKey = (roomCode: string) => `sea-cast-audience:${roomCode}`;

function loadStored(roomCode: string): StoredJoin | null {
  try {
    const raw = localStorage.getItem(storageKey(roomCode));
    return raw ? (JSON.parse(raw) as StoredJoin) : null;
  } catch {
    return null; // private mode / corrupt entry — behave like a first visit
  }
}

function saveStored(roomCode: string, value: StoredJoin): void {
  try {
    localStorage.setItem(storageKey(roomCode), JSON.stringify(value));
  } catch {
    // Storage unavailable: everything still works, minus reload-resume.
  }
}

function isFresh(stored: StoredJoin | null): stored is StoredJoin {
  return stored !== null && stored.expires_at > Date.now() + TOKEN_SLACK_MS;
}

function rememberJoin(roomCode: string, joined: JoinResponse): void {
  saveStored(roomCode, {
    token: joined.token,
    expires_at: joined.expires_at,
    languages: joined.languages,
    source_language: joined.source_language,
    include_source: joined.include_source,
    lang: loadStored(roomCode)?.lang,
  });
}

export function loadStoredLang(roomCode: string): string | null {
  return loadStored(roomCode)?.lang ?? null;
}

export function saveStoredLang(roomCode: string, lang: string | null): void {
  const stored = loadStored(roomCode);
  if (stored) saveStored(roomCode, { ...stored, lang: lang ?? undefined });
}

export type RoomTokenState =
  | { kind: "joining" }
  | { kind: "gone" } // 404 — ended or never existed; terminal
  | { kind: "error" } // transient (network/5xx); retryable
  | {
      kind: "ready";
      languages: string[];
      sourceLanguage: string;
      includeSource: boolean;
    };

export function useRoomToken(roomCode: string): {
  state: RoomTokenState;
  getToken: GetToken;
  retry: () => void;
} {
  const [state, setState] = useState<RoomTokenState>(() => initialState(roomCode));

  // Render-time reset on room change (the route component isn't remounted
  // when only the URL param changes).
  const [prevCode, setPrevCode] = useState(roomCode);
  if (prevCode !== roomCode) {
    setPrevCode(roomCode);
    setState(initialState(roomCode));
  }

  useEffect(() => {
    if (state.kind !== "joining") return;
    let cancelled = false;
    joinRoom(roomCode)
      .then((joined) => {
        if (cancelled) return;
        rememberJoin(roomCode, joined);
        setState({
          kind: "ready",
          languages: joined.languages,
          sourceLanguage: joined.source_language,
          includeSource: joined.include_source,
        });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setState(
          err instanceof ApiError && err.status === 404
            ? { kind: "gone" }
            : { kind: "error" },
        );
      });
    return () => {
      cancelled = true;
    };
  }, [roomCode, state.kind]);

  const getToken = useCallback<GetToken>(
    async (opts) => {
      const stored = loadStored(roomCode);
      if (!opts?.forceRefresh && isFresh(stored)) return stored.token;
      const joined = await joinRoom(roomCode); // ApiError(404) → socket ends
      rememberJoin(roomCode, joined);
      return joined.token;
    },
    [roomCode],
  );

  const retry = useCallback(() => setState({ kind: "joining" }), []);

  return { state, getToken, retry };
}

function initialState(roomCode: string): RoomTokenState {
  const stored = loadStored(roomCode);
  if (isFresh(stored)) {
    return {
      kind: "ready",
      languages: stored.languages,
      sourceLanguage: stored.source_language,
      includeSource: stored.include_source,
    };
  }
  return { kind: "joining" };
}
