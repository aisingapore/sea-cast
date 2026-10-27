// Shown in the Session tab while a roomed session is live: the QR code the
// audience scans, the join URL with a copy button, live listener counts per
// language (5s poll of GET /rooms/{id} — plain polling, not RTVI push), and a
// link to the full-screen stage monitor.

import { useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";

import { getRoom } from "../lib/api";
import { SOURCE_LANG, type RoomInfo } from "../lib/captionTypes";
import { languageName } from "../lib/languageHelpers";

const POLL_MS = 5_000;

export function RoomPanel({
  roomId,
  languages,
}: {
  roomId: string;
  languages: string[];
}) {
  const [info, setInfo] = useState<RoomInfo | null>(null);
  const [copied, setCopied] = useState(false);

  // The QR encodes the *frontend* origin — /join/… is a frontend route (served
  // by Vite in dev, or by the backend's index.html fallback when it serves the
  // built app).
  const joinUrl = `${window.location.origin}/join/${roomId}`;

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      getRoom(roomId)
        .then((room) => {
          if (!cancelled) setInfo(room);
        })
        .catch(() => {
          /* keep the last counts on a failed poll */
        });
    };
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [roomId]);

  const copyJoinUrl = async () => {
    try {
      await navigator.clipboard.writeText(joinUrl);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (http, permissions) — the URL is visible anyway */
    }
  };

  return (
    <aside className="room-panel">
      <div className="room-qr">
        <QRCodeSVG value={joinUrl} size={116} marginSize={2} />
      </div>
      <div className="room-meta">
        <div className="room-code">
          Audience room <strong>{roomId}</strong>
        </div>
        <div className="room-join-url">
          <code>{joinUrl}</code>
          <button
            type="button"
            className="room-copy"
            onClick={() => void copyJoinUrl()}
          >
            {copied ? "Copied ✓" : "Copy"}
          </button>
        </div>
        <div className="room-listeners">
          {(info?.languages ?? languages).map((lang) => (
            <span key={lang} className="listener-chip">
              {languageName(lang)}
              <strong>{info?.listeners?.[lang] ?? 0}</strong>
            </span>
          ))}
          {info?.include_source && (
            <span className="listener-chip">
              {languageName(info.source_language)} (source)
              <strong>{info.listeners?.[SOURCE_LANG] ?? 0}</strong>
            </span>
          )}
        </div>
        <a
          className="room-monitor-link"
          href={`/monitor/${roomId}`}
          target="_blank"
          rel="noreferrer"
        >
          Open stage monitor ↗
        </a>
      </div>
    </aside>
  );
}
