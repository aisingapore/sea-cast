// First-visit language choice: one big tap target per channel the speaker
// offered for this room. The choice is remembered per room (localStorage), so
// a reload or reconnect skips straight to the captions. The source channel (if
// offered) is labelled with the spoken language, e.g. "English (source)".

import { SOURCE_LANG } from "../lib/captionTypes";
import { languageName } from "../lib/languageHelpers";

export function LanguagePicker({
  roomCode,
  languages,
  sourceLanguage,
  onPick,
}: {
  roomCode: string;
  languages: string[];
  sourceLanguage?: string;
  onPick: (lang: string) => void;
}) {
  return (
    <div className="aud-picker">
      <h1>Room {roomCode}</h1>
      <p>Choose your caption language</p>
      {languages.map((lang) => (
        <button
          key={lang}
          type="button"
          className="lang-btn"
          onClick={() => onPick(lang)}
        >
          {lang === SOURCE_LANG
            ? `${sourceLanguage ? languageName(sourceLanguage) : "Original"} (source)`
            : languageName(lang)}
        </button>
      ))}
    </div>
  );
}
