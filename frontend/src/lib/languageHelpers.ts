import {
  APP_SOURCE,
  APP_TARGET,
  AUTO,
  DISPLAY_OVERRIDES,
  type InputLanguage,
  type TargetLanguage,
} from "./languages";

export { AUTO };
export type { InputLanguage };

const displayNames = new Intl.DisplayNames(["en"], { type: "language" });

/** English display name for a canonical code (AUTO → "Auto-detect"). */
export function languageName(code: string): string {
  if (code === AUTO) return "Auto-detect";
  return DISPLAY_OVERRIDES[code] ?? displayNames.of(code) ?? code;
}

/** Target-language options for the dashboard picker: {code, human label}. */
export const LANG_OPTIONS: { value: TargetLanguage; label: string }[] = APP_TARGET.map(
  (value) => ({ value, label: languageName(value) }),
);

/** An STT profile from GET /stt-profiles. */
export type SttProfile = {
  id: string;
  label: string;
  adapter: "openai" | "elevenlabs";
  streaming: boolean;
  languages: { recognizes: string[]; supports_auto: boolean; auto_only: boolean };
  /** Seeds for the VAD dials: Silero's for batch, ElevenLabs' for streaming. */
  vad_defaults: {
    silence_secs: number;
    confidence?: number;
    threshold?: number;
    max_segment_secs?: number;
  };
};

/** A translator profile from GET /translators. */
export type TranslatorProfile = {
  id: string;
  label: string;
  translates_to: string[];
};

function supportsAuto(profile: SttProfile | undefined): boolean {
  return profile?.languages.supports_auto ?? false;
}

export function isAutoOnly(profile: SttProfile | undefined): boolean {
  return profile?.languages.auto_only ?? false;
}

/**
 * Spoken-language dropdown options for a profile, each with a disabled flag.
 * Shows every app source language plus "Auto", greying out what the profile
 * can't do.
 */
export function inputLanguageOptions(
  profile: SttProfile | undefined,
): { value: string; label: string; disabled: boolean }[] {
  const recognizes = new Set(profile?.languages.recognizes ?? []);
  const autoOnly = isAutoOnly(profile);
  return [
    { value: AUTO, label: languageName(AUTO), disabled: !supportsAuto(profile) },
    ...APP_SOURCE.map((code) => ({
      value: code,
      label: languageName(code),
      disabled: autoOnly || !recognizes.has(code),
    })),
  ];
}

/**
 * A valid spoken language for a profile when switching to it: auto-capable
 * profiles default to "Auto".
 */
export function nextInputLanguage(
  profile: SttProfile | undefined,
  current: string,
): InputLanguage {
  if (supportsAuto(profile)) return AUTO;
  const recognized = profile?.languages.recognizes ?? [];
  if (current !== AUTO && recognized.includes(current)) {
    return current as InputLanguage;
  }
  return (recognized[0] ?? "en") as InputLanguage;
}

/**
 * App targets the selected translator cannot translate are not selectable.
 */
export function unavailableTargets(
  translator: TranslatorProfile | undefined,
): ReadonlySet<string> {
  if (!translator) return new Set();
  const covered = new Set(translator.translates_to);
  return new Set(APP_TARGET.filter((code) => !covered.has(code)));
}

/**
 * Drop now-unavailable languages from a selection. Falls back to the first
 * producible target if everything was pruned.
 */
export function pruneTargets(
  targets: TargetLanguage[],
  translator: TranslatorProfile | undefined,
): TargetLanguage[] {
  const unavailable = unavailableTargets(translator);
  const kept = targets.filter((code) => !unavailable.has(code));
  return kept.length
    ? kept
    : APP_TARGET.filter((code) => !unavailable.has(code)).slice(0, 1);
}

/** Comma-separated names an auto-only profile detects (for the info tooltip). */
export function autoDetectedText(profile: SttProfile | undefined): string {
  return (profile?.languages.recognizes ?? []).map(languageName).join(", ");
}
