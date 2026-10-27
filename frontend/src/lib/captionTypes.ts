// The shapes the backend sends and accepts: the captions WebSocket, rooms,
// and the admin history API (app/routes/rooms.py, app/routes/admin.py).
// Change them together with the backend.

// "created" = the speaker's mic isn't connected yet (show "waiting…");
// "ended" is terminal — stop reconnecting.
export type RoomStatus = "created" | "live" | "ended";

// ---- WS: /rooms/{room_id}/captions?lang={lang}&token={jwt} ----

// The channel carrying the untranslated transcript. The stage monitor always
// shows it; audiences can pick it only when the room's `include_source` is on.
// It's never listed in `languages`.
export const SOURCE_LANG = "source";

export type CaptionMessage = {
  type: "caption";
  lang: string; // language code, or SOURCE_LANG
  text: string; // full accumulated hypothesis — each partial replaces the last
  final: boolean;
  utterance_id: string; // stable across partial→final for one utterance
  seq: number; // increments per partial within an utterance; discard stale
  ts: number; // epoch milliseconds
  replay?: boolean; // true for entries replayed from history on (re)connect
};

export type RoomStatusMessage = {
  type: "room_status";
  status: RoomStatus;
  languages: string[]; // target languages chosen by the speaker
  listeners?: Record<string, number>; // lang -> live listener count
  ts: number;
};

export type PingMessage = { type: "ping"; ts: number };

export type CaptionSocketMessage = CaptionMessage | RoomStatusMessage | PingMessage;

// WS close code sent by the server when the join token is invalid or expired;
// the client should re-join (POST /rooms/{id}/join) and reconnect.
export const CLOSE_TOKEN_INVALID = 4001;

// ---- HTTP: rooms ----

// POST /rooms  (speaker dashboard, before connecting the mic transport)
export type CreateRoomRequest = {
  target_languages: string[];
  input_language: string;
  // Offer the untranslated source transcript to audiences as its own channel.
  include_source?: boolean;
};
export type CreateRoomResponse = {
  room_id: string; // 6-char code — goes in the QR / join URL
  languages: string[];
  include_source: boolean;
  source_language: string;
};

// GET /rooms/{room_id}  (dashboard room panel: listener counts)
export type RoomInfo = {
  room_id: string;
  status: RoomStatus;
  languages: string[];
  listeners: Record<string, number>;
  include_source: boolean;
  source_language: string;
};

// POST /rooms/{room_id}/join  (audience; no body, anonymous)
export type JoinResponse = {
  room_id: string;
  token: string; // JWT for the captions WS
  expires_at: number; // epoch ms — re-join when stale
  languages: string[];
  source_language: string;
  // When true, the audience may also pick the untranslated source channel.
  include_source: boolean;
  status: RoomStatus; // never "ended": joining an ended room returns 404
};

// ---- HTTP: admin history ----

// POST /admin/login  (History page; trades the shared admin credential for a
// short-lived token that gates the /sessions* endpoints).
export type AdminToken = {
  token: string; // Bearer token / ?token= for /sessions*
  expires_at: number; // epoch ms — re-login when stale
};

// GET /sessions  — one row per recorded session (admin-only).
export type SessionSummary = {
  id: string;
  created_at?: string | null;
  duration_secs?: number;
  transcriber?: string;
  input_language?: string;
  target_language?: string;
  translators?: string[];
};

// GET /sessions/{id}  — full transcript + translations + audio flag.
export type SessionDetail = SessionSummary & {
  transcript: string[];
  translations: { label: string; lines: string[] }[];
  has_audio: boolean;
};
