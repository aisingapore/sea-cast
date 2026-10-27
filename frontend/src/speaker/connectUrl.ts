import { wsBase } from "../lib/api";
import type { CreateRoomResponse } from "../lib/captionTypes";
import type { SessionSetup } from "./useSessionSetup";

// The speaker's /ws URL: the session's choices as query params, which the
// server checks in routes/speaker.py.
export function buildConnectUrl(
  setup: SessionSetup,
  sessionId: string,
  room: CreateRoomResponse | null,
): string {
  const { sttProfile, dials } = setup;
  const url = new URL(`${wsBase()}/ws`);
  url.searchParams.set("stt_profile", sttProfile?.id ?? "");
  url.searchParams.set("input_language", setup.inputLanguage);
  url.searchParams.set("target_language", setup.targetLanguages[0]);
  // No room (creation failed) means a speaker-only session: primary language only.
  if (room) {
    url.searchParams.set("target_languages", setup.targetLanguages.join(","));
    url.searchParams.set("room_id", room.room_id);
  }
  url.searchParams.set("session_id", sessionId);
  url.searchParams.set("translator", setup.translator?.id ?? "");
  url.searchParams.set("noise_filter", setup.noiseFilter);
  if (setup.glossaryLanguages) {
    url.searchParams.set("glossary", String(setup.useGlossary));
  }
  // VAD dials: streaming profiles endpoint server-side (silence + threshold);
  // batch profiles endpoint via Silero in-pipeline (silence + confidence).
  if (sttProfile?.streaming) {
    url.searchParams.set("vad_silence_secs", String(dials.silenceSecs));
    url.searchParams.set("vad_threshold", String(dials.threshold));
  } else if (sttProfile) {
    url.searchParams.set("vad_silence_secs", String(dials.silenceSecs));
    url.searchParams.set("vad_confidence", String(dials.confidence));
    url.searchParams.set("max_segment_secs", String(dials.maxSegmentSecs));
  }
  return url.toString();
}
