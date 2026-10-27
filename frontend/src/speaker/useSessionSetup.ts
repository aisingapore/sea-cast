import { useCallback, useEffect, useState } from "react";
import { serverUrl as resolveServerUrl } from "../lib/api";
import type { TargetLanguage } from "../lib/languages";
import {
  AUTO,
  type InputLanguage,
  nextInputLanguage,
  pruneTargets,
  type SttProfile,
  type TranslatorProfile,
} from "../lib/languageHelpers";

export type NoiseFilter = "off" | "rnnoise";

// Per-session VAD dials (Settings tab), sent as query params at connect.
// Reseeded from the selected STT profile's vad_defaults whenever it changes.
export type VadDials = {
  silenceSecs: number;
  confidence: number; // batch profiles only
  threshold: number; // streaming profiles only
  // Batch profiles only: longest continuous speech before a forced flush (0 = off).
  maxSegmentSecs: number;
};

// Everything the speaker picks before Start: the STT and translator profiles
// (from GET /stt-profiles and GET /translators, both read from sea-cast.yaml),
// the languages, the Source channel and Glossary checkboxes, and the Settings
// tab. All of it is sent as /ws query params at connect (see connectUrl.ts).
export function useSessionSetup() {
  const serverUrl = resolveServerUrl();
  // Configured STT profiles and the selected one.
  const [sttProfiles, setSttProfiles] = useState<SttProfile[]>([]);
  const [sttProfileId, setSttProfileId] = useState<string>("");
  // Reset to one the selected profile supports when profiles load or change.
  const [inputLanguage, setInputLanguage] = useState<InputLanguage>(AUTO);
  // Ordered fan-out list; [0] = primary (drives the on-screen column and the
  // recorder). Extras are audience-only.
  const [targetLanguages, setTargetLanguages] = useState<TargetLanguage[]>(["id"]);
  // Offer the untranslated source transcript to the audience as its own channel.
  const [includeSource, setIncludeSource] = useState(false);
  // Configured translator profiles and the selected one.
  const [translatorProfiles, setTranslatorProfiles] = useState<TranslatorProfile[]>([]);
  const [translatorId, setTranslatorId] = useState<string>("");
  // The languages of the server's glossary (from GET /glossary), or null when it
  // has none; the Glossary toggle is inactive without one. On by default.
  const [glossaryLanguages, setGlossaryLanguages] = useState<string[] | null>(null);
  const [useGlossary, setUseGlossary] = useState(true);
  // Background-noise filter, applied at the transport input for every STT
  // profile. Defaults to "off" to match the server default (clean mics + <1.5s
  // latency).
  const [noiseFilter, setNoiseFilter] = useState<NoiseFilter>("off");
  const [dials, setDials] = useState<VadDials>({
    silenceSecs: 0.6,
    confidence: 0.7,
    threshold: 0.4,
    maxSegmentSecs: 8,
  });

  // The selected profiles (undefined until the lists load).
  const sttProfile = sttProfiles.find((p) => p.id === sttProfileId);
  const translator = translatorProfiles.find((p) => p.id === translatorId);

  // Select an STT profile: reset the spoken language to one it supports and
  // seed the VAD dials from its defaults (the silence dial drives Silero for
  // batch profiles and ElevenLabs' commit silence for streaming ones).
  const selectSttProfile = useCallback((profile: SttProfile | undefined) => {
    setSttProfileId(profile?.id ?? "");
    setInputLanguage((cur) => nextInputLanguage(profile, cur));
    if (!profile) return;
    const d = profile.vad_defaults;
    setDials((cur) => ({
      silenceSecs: d.silence_secs,
      confidence: d.confidence ?? cur.confidence,
      threshold: d.threshold ?? cur.threshold,
      maxSegmentSecs: d.max_segment_secs ?? cur.maxSegmentSecs,
    }));
  }, []);

  // Select a translator. A restricted one (e.g. an English-only profile) may not
  // cover every selected target, so prune those.
  const selectTranslator = useCallback((profile: TranslatorProfile | undefined) => {
    setTranslatorId(profile?.id ?? "");
    setTargetLanguages((cur) => pruneTargets(cur, profile));
  }, []);

  // Source ≠ target: drop the new spoken language from the targets if present
  // (keep at least one), so we never fan a language out into itself.
  const selectInputLanguage = useCallback((next: InputLanguage) => {
    setInputLanguage(next);
    setTargetLanguages((cur) => {
      const kept = cur.filter((l) => l !== next);
      return kept.length === cur.length || kept.length === 0 ? cur : kept;
    });
  }, []);

  // Load the configured STT profiles once; default to the first.
  useEffect(() => {
    let cancelled = false;
    fetch(`${serverUrl}/stt-profiles`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list: SttProfile[]) => {
        if (cancelled) return;
        setSttProfiles(list);
        selectSttProfile(list[0]);
      })
      .catch(() => {
        /* leave the dropdown empty; the backend falls back to its default */
      });
    return () => {
      cancelled = true;
    };
  }, [serverUrl, selectSttProfile]);

  // Load the configured translators once; default to the first.
  useEffect(() => {
    let cancelled = false;
    fetch(`${serverUrl}/translators`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list: TranslatorProfile[]) => {
        if (cancelled) return;
        setTranslatorProfiles(list);
        selectTranslator(list[0]);
      })
      .catch(() => {
        /* leave the dropdown empty; the backend falls back to its default */
      });
    return () => {
      cancelled = true;
    };
  }, [serverUrl, selectTranslator]);

  // Check once whether the server has a glossary.
  useEffect(() => {
    let cancelled = false;
    fetch(`${serverUrl}/glossary`)
      .then((r) => (r.ok ? r.json() : { available: false }))
      .then((d: { available: boolean; languages?: string[] }) => {
        if (cancelled) return;
        setGlossaryLanguages(d.available ? (d.languages ?? []) : null);
      })
      .catch(() => {
        /* no toggle; the server uses its glossary, if any, by default */
      });
    return () => {
      cancelled = true;
    };
  }, [serverUrl]);

  return {
    sttProfiles,
    sttProfile,
    selectSttProfile,
    inputLanguage,
    selectInputLanguage,
    targetLanguages,
    setTargetLanguages,
    includeSource,
    setIncludeSource,
    translatorProfiles,
    translator,
    selectTranslator,
    glossaryLanguages,
    useGlossary,
    setUseGlossary,
    noiseFilter,
    setNoiseFilter,
    dials,
    setDials,
  };
}

export type SessionSetup = ReturnType<typeof useSessionSetup>;
