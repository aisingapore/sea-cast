import { useCallback, useState } from "react";
import {
  RTVIEvent,
  type BotLLMTextData,
  type PipecatMetricsData,
  type TranscriptData,
} from "@pipecat-ai/client-js";
import { useRTVIClientEvent } from "@pipecat-ai/client-react";
import { mergeMetrics, type MetricsStore } from "./metrics";

export type Transcript = { text: string; final: boolean };
export type SttError = { message: string };

// What the server sends back during a session, over RTVI: the transcript, the
// primary language's translation (used when there's no room), latency metrics
// and STT errors.
export function useSessionFeed() {
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [translations, setTranslations] = useState<string[]>([]);
  const [metrics, setMetrics] = useState<MetricsStore>({});
  const [sttError, setSttError] = useState<SttError | null>(null);

  const reset = useCallback(() => {
    setTranscripts([]);
    setTranslations([]);
    setMetrics({});
    setSttError(null);
  }, []);

  const dismissSttError = useCallback(() => setSttError(null), []);

  const handleUserTranscript = useCallback((data: TranscriptData) => {
    // A fresh transcript means STT recovered — clear any lingering error banner.
    setSttError(null);
    setTranscripts((prev) => {
      const last = prev.at(-1);
      if (last && !last.final) {
        return [...prev.slice(0, -1), { text: data.text, final: data.final }];
      }
      return [...prev, { text: data.text, final: data.final }];
    });
  }, []);

  const handleBotLlmStarted = useCallback(() => {
    setTranslations((prev) => [...prev, ""]);
  }, []);

  const handleBotLlmText = useCallback((data: BotLLMTextData) => {
    setTranslations((prev) => {
      if (prev.length === 0) return [data.text];
      return [...prev.slice(0, -1), (prev.at(-1) ?? "") + data.text];
    });
  }, []);

  const handleMetrics = useCallback((data: PipecatMetricsData) => {
    setMetrics((prev) => mergeMetrics(prev, data));
  }, []);

  // A failed STT request arrives as a custom RTVI server message
  // ({ type: "stt_error", message }), with a generic message: the details stay in
  // the server log. Show it as a dismissible banner.
  const handleSttError = useCallback((data: unknown) => {
    const msg = data as { type?: string; message?: string };
    if (msg?.type !== "stt_error") return;
    setSttError({ message: msg.message ?? "Speech-to-text failed." });
  }, []);

  useRTVIClientEvent(RTVIEvent.UserTranscript, handleUserTranscript);
  useRTVIClientEvent(RTVIEvent.BotLlmStarted, handleBotLlmStarted);
  useRTVIClientEvent(RTVIEvent.BotLlmText, handleBotLlmText);
  useRTVIClientEvent(RTVIEvent.Metrics, handleMetrics);
  useRTVIClientEvent(RTVIEvent.ServerMessage, handleSttError);

  return { transcripts, translations, metrics, sttError, dismissSttError, reset };
}
