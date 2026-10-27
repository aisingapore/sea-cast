// React hook: subscribe to one room+language caption channel and expose the
// folded caption lines, the latest room status, and the connection state.
//
// Owns a CaptionsSocket for the lifetime of (roomId, lang) — switching
// language closes the old socket and opens a fresh one (server replays the
// last 90s on connect, so the ticker refills immediately).
//
// NOTE: `getToken` is an effect dependency — memoize it in the caller
// (useCallback / module fn) or the socket reconnects on every render.

import { useEffect, useState } from "react";

import { captionsSocketUrl } from "./api";
import { applyCaption, emptyFeed, type CaptionLine } from "./captionFeed";
import {
  CaptionsSocket,
  type CaptionsSocketStatus,
  type GetToken,
} from "./captionsSocket";
import type { RoomStatusMessage } from "./captionTypes";

export type CaptionFeed = {
  lines: CaptionLine[];
  roomStatus: RoomStatusMessage | null;
  connection: CaptionsSocketStatus;
};

export function useCaptionFeed(
  roomId: string,
  lang: string,
  getToken: GetToken,
): CaptionFeed {
  const [lines, setLines] = useState<CaptionLine[]>(emptyFeed);
  const [roomStatus, setRoomStatus] = useState<RoomStatusMessage | null>(null);
  const [connection, setConnection] = useState<CaptionsSocketStatus>("connecting");

  // Reset the feed when the channel changes — done during render (the
  // documented "adjust state on prop change" pattern) so the first paint of a
  // new language never shows the previous language's lines.
  const channel = `${roomId} ${lang}`;
  const [prevChannel, setPrevChannel] = useState(channel);
  if (prevChannel !== channel) {
    setPrevChannel(channel);
    setLines(emptyFeed());
    setRoomStatus(null);
  }

  useEffect(() => {
    const socket = new CaptionsSocket({
      socketUrl: (token) => captionsSocketUrl(roomId, lang, token),
      getToken,
      onMessage: (msg) => {
        if (msg.type === "caption") {
          setLines((prev) => applyCaption(prev, msg));
        } else if (msg.type === "room_status") {
          setRoomStatus(msg);
        }
        // pings only feed the socket's own stale timer
      },
      onStatus: setConnection,
    });
    socket.connect();
    return () => socket.close();
  }, [roomId, lang, getToken]);

  return { lines, roomStatus, connection };
}
