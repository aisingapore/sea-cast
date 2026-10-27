import {
  autoDetectedText,
  type InputLanguage,
  inputLanguageOptions,
  isAutoOnly,
  languageName,
  unavailableTargets,
} from "../lib/languageHelpers";
import { InfoTip } from "./InfoTip";
import { MicSelect } from "./MicSelect";
import { MicWaveform } from "./MicWaveform";
import { TargetLanguagesSelect } from "./TargetLanguagesSelect";
import type { SessionConnection } from "./useSessionConnection";
import type { SessionSetup } from "./useSessionSetup";

// The bar above the tabs: the session's choices, the mic, Start/Stop and the
// connection state. The choices are read-only once live, so the bar collapses
// to the action row unless the speaker expands it.
export function SessionControls({
  setup,
  connection,
  onStart,
  settingsOpen,
  onToggleSettings,
}: {
  setup: SessionSetup;
  connection: SessionConnection;
  onStart: () => void;
  settingsOpen: boolean;
  onToggleSettings: () => void;
}) {
  const { sttProfile, translator, glossaryLanguages } = setup;
  const { transportState, isActive, isBusy, isStopping } = connection;

  const btnLabel = isStopping
    ? "Stopping…"
    : isActive
      ? "Stop"
      : isBusy
        ? "Connecting…"
        : "Start";

  // Idle → always show the fields; live → hide unless the user expands them.
  const inSession = isActive || isBusy;
  const showSettings = !inSession || settingsOpen;
  const isCollapsed = inSession && !settingsOpen;

  return (
    <div className="controls">
      {showSettings && (
        <>
          <label className="control-group">
            <span>Transcriber</span>
            <select
              className="profile-select"
              title={sttProfile?.label}
              disabled={inSession}
              value={sttProfile?.id ?? ""}
              onChange={(e) =>
                setup.selectSttProfile(
                  setup.sttProfiles.find((p) => p.id === e.target.value),
                )
              }
            >
              {setup.sttProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="control-group">
            <span className="control-head">
              Spoken language
              {isAutoOnly(sttProfile) && (
                <InfoTip
                  label={`${sttProfile?.label} auto-detects the spoken language. Supported: ${autoDetectedText(sttProfile)}.`}
                >
                  Auto-detects the spoken language — supports{" "}
                  {autoDetectedText(sttProfile)}.
                </InfoTip>
              )}
            </span>
            <select
              disabled={inSession}
              value={setup.inputLanguage}
              onChange={(e) =>
                setup.selectInputLanguage(e.target.value as InputLanguage)
              }
            >
              {inputLanguageOptions(sttProfile).map((o) => (
                <option key={o.value} value={o.value} disabled={o.disabled}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <TargetLanguagesSelect
            value={setup.targetLanguages}
            onChange={setup.setTargetLanguages}
            disabled={inSession}
            sourceLanguage={setup.inputLanguage}
            unavailable={unavailableTargets(translator)}
          />
          <label className="control-group control-check">
            <span>Source channel</span>
            <span className="check-row">
              <input
                type="checkbox"
                checked={setup.includeSource}
                disabled={inSession}
                title="Also offer the untranslated source transcript to the audience as its own channel"
                onChange={(e) => setup.setIncludeSource(e.target.checked)}
              />
            </span>
          </label>
          {/* Inactive (and unticked) when the server has no glossary. */}
          <label className="control-group control-check">
            <span>Glossary</span>
            <span className="check-row">
              <input
                type="checkbox"
                checked={glossaryLanguages !== null && setup.useGlossary}
                disabled={glossaryLanguages === null || inSession}
                title={
                  glossaryLanguages === null
                    ? "No glossary configured."
                    : `Provide the translator with translations of glossary terms (${glossaryLanguages.map(languageName).join(", ")})`
                }
                onChange={(e) => setup.setUseGlossary(e.target.checked)}
              />
            </span>
          </label>
          <label className="control-group">
            <span>Translator</span>
            <select
              className="profile-select"
              title={translator?.label}
              disabled={inSession}
              value={translator?.id ?? ""}
              onChange={(e) =>
                setup.selectTranslator(
                  setup.translatorProfiles.find((p) => p.id === e.target.value),
                )
              }
            >
              {setup.translatorProfiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <MicSelect />
        </>
      )}
      <MicWaveform />
      <button
        className={`btn ${isActive && !isStopping ? "btn-stop" : "btn-start"}`}
        onClick={isActive ? connection.stop : onStart}
        disabled={isBusy || isStopping}
      >
        {btnLabel}
      </button>
      <span className="transport-badge" data-state={transportState}>
        {transportState}
      </span>
      {isCollapsed && (
        <span className="controls-summary">
          {languageName(setup.inputLanguage)} →{" "}
          {setup.targetLanguages.map(languageName).join(", ")}
        </span>
      )}
      {inSession && (
        <button type="button" className="controls-toggle" onClick={onToggleSettings}>
          {settingsOpen ? "Hide settings ▴" : "Show settings ▾"}
        </button>
      )}
    </div>
  );
}
