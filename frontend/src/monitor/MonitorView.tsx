// Stage monitor at /monitor/:roomCode — a full-screen ticker of the raw
// transcript (the "source" pseudo-language) for the speaker to glance at on
// stage. Same join/token flow and ticker as the audience, larger type, no
// language picker.

import { useEffect } from "react";
import { useParams } from "react-router";

import { SOURCE_LANG } from "../lib/captionTypes";
import { useCaptionFeed } from "../lib/useCaptionFeed";
import type { GetToken } from "../lib/captionsSocket";
import { useRoomToken } from "../lib/useRoomToken";
import { CaptionTicker } from "../audience/CaptionTicker";
import { StatusBanner } from "../audience/StatusBanner";
import "../audience/audience.css";

export default function MonitorView() {
  const { roomCode = "" } = useParams();
  const code = roomCode.toUpperCase();
  const { state, getToken, retry } = useRoomToken(code);

  useEffect(() => {
    document.title = `SEA-CAST monitor · ${code}`;
  }, [code]);

  return (
    <div className="aud-root monitor">
      {state.kind === "joining" && (
        <div className="aud-center">Joining room {code}…</div>
      )}
      {state.kind === "gone" && (
        <div className="aud-center">
          This session has ended, or the room code is wrong.
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
      {state.kind === "ready" && <MonitorStream code={code} getToken={getToken} />}
    </div>
  );
}

function MonitorStream({ code, getToken }: { code: string; getToken: GetToken }) {
  const { lines, roomStatus, connection } = useCaptionFeed(code, SOURCE_LANG, getToken);
  const ended = connection === "ended" || roomStatus?.status === "ended";

  return (
    <>
      <header className="aud-bar">
        <span className="aud-brand">SEA-CAST · transcript monitor</span>
        <span className="aud-room">{code}</span>
      </header>
      <StatusBanner connection={connection} ended={ended} />
      {lines.length > 0 ? (
        <CaptionTicker lines={lines} />
      ) : (
        <div className="aud-center">
          {ended ? "Session ended." : "Waiting for speech…"}
        </div>
      )}
    </>
  );
}
