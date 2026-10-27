// Target-language control for the dashboard: a single multi-select of the
// supported languages as toggle chips. The value is the ordered fan-out list;
// index 0 is the primary — it drives the one on-screen translation column, the
// recorder's translation file. Every
// selected language is still recorded, measured, and broadcast to the audience.
//
// The spoken (source) language is excluded: translating a language into itself
// only wastes tokens — the "source channel" option shows it untranslated.

import { LANG_OPTIONS } from "../lib/languageHelpers";
import type { TargetLanguage } from "../lib/languages";

export function TargetLanguagesSelect({
  value,
  onChange,
  disabled,
  sourceLanguage,
  unavailable,
}: {
  value: TargetLanguage[]; // ordered; [0] = primary, always present
  onChange: (next: TargetLanguage[]) => void;
  disabled?: boolean;
  // The spoken language — never offered as a target (source ≠ target).
  sourceLanguage?: string;
  // Languages the active translator can't produce at all (e.g. an English-only
  // LLM profile) — not selectable as primary or extra.
  unavailable?: ReadonlySet<string>;
}) {
  const toggle = (lang: TargetLanguage): void => {
    if (unavailable?.has(lang)) return; // translator can't produce it
    if (value.includes(lang)) {
      if (value.length === 1) return; // keep at least one target
      onChange(value.filter((l) => l !== lang));
    } else {
      onChange([...value, lang]);
    }
  };

  const options = LANG_OPTIONS.filter((o) => o.value !== sourceLanguage);

  return (
    <div className="control-group lang-chips-group">
      <span>Translate to</span>
      <div className="lang-chips">
        {options.map((o) => {
          const selected = value.includes(o.value);
          const chipDisabled = disabled || !!unavailable?.has(o.value);
          const isPrimary = selected && value[0] === o.value;
          return (
            <button
              key={o.value}
              type="button"
              className={`lang-chip${selected ? " selected" : ""}`}
              disabled={chipDisabled}
              aria-pressed={selected}
              title={
                isPrimary && value.length > 1
                  ? "Primary — shown live on the dashboard"
                  : undefined
              }
              onClick={() => toggle(o.value)}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
