import { useCallback, useRef, useState } from "react";
import {
  usePipecatClient,
  usePipecatClientTransportState,
} from "@pipecat-ai/client-react";
import { createRoom, serverUrl as resolveServerUrl } from "../lib/api";
import type { CreateRoomResponse } from "../lib/captionTypes";
import { buildConnectUrl } from "./connectUrl";
import type { SessionSetup } from "./useSessionSetup";

// Starting and stopping the speaker's session. Handles the audience room,
// the WebSocket connection and the drain on Stop.
export function useSessionConnection() {
  const client = usePipecatClient();
  const transportState = usePipecatClientTransportState();
  // The audience room of the current session, created on Start. null before the
  // first session, or when creating it failed (the session then runs without an
  // audience).
  const [room, setRoom] = useState<CreateRoomResponse | null>(null);
  const [stopping, setStopping] = useState(false);
  // The current session's ID, which stop() sends to /stop-session.
  const sessionIdRef = useRef<string | null>(null);
  const serverUrl = resolveServerUrl();

  // True from the Stop click until the connection has closed (the button shows
  // "Stopping…"). `stopping` stays true after that, but nothing reads it until
  // the next Start resets it.
  const isStopping = stopping && transportState !== "disconnected";

  const isActive = transportState === "ready" || transportState === "connected";
  const isBusy =
    transportState === "initializing" ||
    transportState === "authenticating" ||
    transportState === "authenticated" ||
    transportState === "connecting" ||
    transportState === "disconnecting";

  const start = useCallback(
    async (setup: SessionSetup) => {
      if (!client) return;
      setStopping(false);
      // One room per session — the audience joins via its QR (RoomPanel).
      // Created before the transport connects so /ws can bind to it. If
      // creation fails the session still runs, just speaker-only.
      let sessionRoom: CreateRoomResponse | null = null;
      try {
        sessionRoom = await createRoom({
          target_languages: setup.targetLanguages,
          input_language: setup.inputLanguage,
          include_source: setup.includeSource,
        });
      } catch (err) {
        console.error("Room creation failed — continuing without a room:", err);
      }
      setRoom(sessionRoom);
      try {
        // Turn the mic back on: stop() turned it off, and the same client is
        // reused for every session. It must be on before connect(), which only
        // starts recording if it is.
        client.enableMic(true);
        // Set up the mic here, not when the page loads: the session it opens
        // expires if the speaker waits before Start, and recording then fails
        // with "Session ended".
        await client.initDevices();
        const sessionId = crypto.randomUUID();
        sessionIdRef.current = sessionId;
        await client.connect({ wsUrl: buildConnectUrl(setup, sessionId, sessionRoom) });
      } catch (err) {
        console.error("Connection error:", err);
      }
    },
    [client],
  );

  const stop = useCallback(async () => {
    setStopping(true);
    const sessionId = sessionIdRef.current;

    if (sessionId) {
      // Stop in three steps. 1) Turn the mic off: Pipecat treats a close as an
      // error while audio is still being sent. 2) Ask the server to finish the
      // translation in progress (drain). It replies when done. 3) Close the
      // connection via the browser. A close triggered by the server would show
      // as an error.
      client?.enableMic(false);
      const stopUrl = new URL(`${serverUrl}/stop-session`);
      stopUrl.searchParams.set("session_id", sessionId);
      try {
        await fetch(stopUrl.toString(), { method: "POST" });
      } catch (err) {
        console.error("Stop-session error:", err);
      }
      sessionIdRef.current = null;
    }
    // Fallback / cleanup: ensures RTVI state resets even if the server
    // already closed the connection or the HTTP call failed.
    await client?.disconnect();
  }, [client, serverUrl]);

  return { transportState, room, isActive, isBusy, isStopping, start, stop };
}

export type SessionConnection = ReturnType<typeof useSessionConnection>;
