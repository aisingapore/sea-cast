// Pure caption-feed reducer: fold incoming caption messages into an ordered
// list of lines. Pure and deterministic (no Date.now) so it's trivially
// testable and replay-safe.
//
// The upsert key is utterance_id — stable partial→final on the server — which
// makes replay dedup automatic: a replayed final and a live final for the same
// utterance collapse into one line. Stale updates (an old partial arriving
// after a newer partial or after the final) are dropped by `seq`/final rules.

import type { CaptionMessage } from "./captionTypes";

export type CaptionLine = {
  utteranceId: string;
  text: string;
  final: boolean;
  seq: number;
  ts: number; // epoch ms of the latest update applied
};

// Prune bounds: keep the ticker to roughly what a phone can meaningfully
// scroll back through. Age is measured against the newest line's ts (not the
// wall clock) so a paused speaker doesn't blank the screen. The backend replays
// the same window to a (re)connecting phone (caption_history_maxlen /
// caption_replay_secs in config.py); change them together.
export const MAX_LINES = 200;
export const MAX_AGE_MS = 90_000;

export function emptyFeed(): CaptionLine[] {
  return [];
}

// Returns the SAME array reference when the message is stale/duplicate, so
// React consumers skip a re-render.
export function applyCaption(
  lines: readonly CaptionLine[],
  msg: CaptionMessage,
): CaptionLine[] {
  const idx = lines.findIndex((l) => l.utteranceId === msg.utterance_id);
  if (idx === -1) {
    return prune([...lines, toLine(msg)]);
  }
  const existing = lines[idx];
  if (!supersedes(msg, existing)) {
    return lines as CaptionLine[];
  }
  const next = lines.slice();
  next[idx] = toLine(msg);
  return prune(next);
}

function supersedes(msg: CaptionMessage, existing: CaptionLine): boolean {
  if (existing.final) {
    // A final is settled; only ignore echoes (e.g. a replayed duplicate).
    return false;
  }
  // Finals always land; partials must be newer than what's shown.
  return msg.final || msg.seq >= existing.seq;
}

function toLine(msg: CaptionMessage): CaptionLine {
  return {
    utteranceId: msg.utterance_id,
    text: msg.text,
    final: msg.final,
    seq: msg.seq,
    ts: msg.ts,
  };
}

function prune(lines: CaptionLine[]): CaptionLine[] {
  const newest = lines.reduce((max, l) => (l.ts > max ? l.ts : max), 0);
  const cutoff = newest - MAX_AGE_MS;
  let out = lines.filter((l) => l.ts >= cutoff);
  if (out.length > MAX_LINES) {
    out = out.slice(out.length - MAX_LINES);
  }
  return out;
}
