// Audience surface at /join/:roomCode (the QR target).
//
// Join state machine: joining → (gone | error | ready), then ready splits into
// "pick a language" and "stream captions". Token + language live in
// localStorage per room (see useRoomToken), so a revisit inside the token's
// lifetime skips both the join round-trip and the picker; network blips are
// the captions socket's problem (it reconnects itself).

import { useEffect, useState } from "react";
import { useParams } from "react-router";

import { SOURCE_LANG } from "../lib/captionTypes";
import { useCaptionFeed } from "../lib/useCaptionFeed";
import type { GetToken } from "../lib/captionsSocket";
import { loadStoredLang, saveStoredLang, useRoomToken } from "../lib/useRoomToken";
import { CaptionTicker } from "./CaptionTicker";
import { LanguagePicker } from "./LanguagePicker";
import { StatusBanner } from "./StatusBanner";
import "./audience.css";

export default function AudienceView() {
  const { roomCode = "" } = useParams();
  // Room codes are uppercase Crockford base32; be forgiving to typed-in URLs.
  const code = roomCode.toUpperCase();

  const { state, getToken, retry } = useRoomToken(code);
  const [lang, setLang] = useState<string | null>(() => loadStoredLang(code));

  useEffect(() => {
    document.title = `SEA-CAST · ${code}`;
  }, [code]);

  // The channels the audience can pick: the room's target languages, plus the
  // untranslated source transcript when the speaker enabled it.
  const channels =
    state.kind === "ready"
      ? state.includeSource
        ? [...state.languages, SOURCE_LANG]
        : state.languages
      : [];
  // Only trust a stored language the room actually offers (the speaker may
  // have started a new room under a recycled code with other languages).
  const activeLang =
    state.kind === "ready" && lang && channels.includes(lang) ? lang : null;

  return (
    <div className="aud-root">
      {state.kind === "joining" && (
        <div className="aud-center">Joining room {code}…</div>
      )}
      {state.kind === "gone" && (
        <div className="aud-center">
          <div>
            <p>This session has ended, or the room code is wrong.</p>
            <p>Scan the QR code again once the speaker starts a new session.</p>
          </div>
        </div>
      )}
      {state.kind === "error" && (
        <div className="aud-center">
          <div>
            <p>Can’t reach the server right now.</p>
            <button type="button" className="lang-btn" onClick={retry}>
              Try again
            </button>
          </div>
        </div>
      )}
      {state.kind === "ready" && activeLang === null && (
        <LanguagePicker
          roomCode={code}
          languages={channels}
          sourceLanguage={state.sourceLanguage}
          onPick={(picked) => {
            saveStoredLang(code, picked);
            setLang(picked);
          }}
        />
      )}
      {state.kind === "ready" && activeLang !== null && (
        <AudienceStream
          code={code}
          lang={activeLang}
          label={
            activeLang === SOURCE_LANG ? `${state.sourceLanguage} (source)` : activeLang
          }
          getToken={getToken}
          canSwitch={channels.length > 1}
          onSwitch={() => {
            saveStoredLang(code, null);
            setLang(null);
          }}
        />
      )}
    </div>
  );
}

// Child component so useCaptionFeed (which opens the socket) only mounts once
// a language is chosen — one socket per language; switching remounts it.
function AudienceStream({
  code,
  lang,
  label,
  getToken,
  canSwitch,
  onSwitch,
}: {
  code: string;
  lang: string;
  label: string;
  getToken: GetToken;
  canSwitch: boolean;
  onSwitch: () => void;
}) {
  const { lines, roomStatus, connection } = useCaptionFeed(code, lang, getToken);
  const ended = connection === "ended" || roomStatus?.status === "ended";

  return (
    <div className="aud-stream">
      <header className="aud-bar">
        <span className="aud-brand">SEA-CAST</span>
        <span className="aud-room">{code}</span>
        <span className="aud-lang">{label}</span>
        {canSwitch && !ended && (
          <button type="button" className="aud-switch" onClick={onSwitch}>
            Change language
          </button>
        )}
      </header>
      <StatusBanner connection={connection} ended={ended} />
      {lines.length > 0 ? (
        <CaptionTicker lines={lines} />
      ) : (
        <div className="aud-center">
          {ended
            ? "Session ended — thanks for joining!"
            : roomStatus?.status === "created"
              ? "Waiting for the speaker to start…"
              : "Waiting for captions…"}
        </div>
      )}
    </div>
  );
}
