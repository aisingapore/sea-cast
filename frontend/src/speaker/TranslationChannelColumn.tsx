// Session-tab translation column with a live language switcher.
//
// The dashboard's RTVI translation stream (BotLlmText) only carries the primary
// language. Every selected language is, however, already fanned out to the
// per-language audience caption channels — so this column subscribes to those
// channels (exactly as the audience does) and lets the speaker flip between
// languages with a chip to spot-check each one is keeping up. One channel is
// open at a time; switching replays the last ~90s for that language. Auto-scroll
// is preserved via the shared useStickToBottom hook.

import { useState } from "react";

import { languageName } from "../lib/languageHelpers";
import { useCaptionFeed } from "../lib/useCaptionFeed";
import { useRoomToken } from "../lib/useRoomToken";
import { useStickToBottom } from "../lib/useStickToBottom";

export function TranslationChannelColumn({
  roomId,
  languages,
}: {
  roomId: string;
  // The room's target languages (ordered; [0] = primary). These are the exact
  // channel names the captions WS accepts.
  languages: string[];
}) {
  const [viewLang, setViewLang] = useState(languages[0]);

  // Reset the viewed language if the selection changes (adjust-state-on-prop-
  // change), and keep it valid if a language is dropped mid-session.
  const [prevLangs, setPrevLangs] = useState(languages);
  if (prevLangs !== languages) {
    setPrevLangs(languages);
    if (!languages.includes(viewLang)) setViewLang(languages[0]);
  }

  // The speaker mints an audience token for their own room (the join endpoint is
  // anonymous); getToken is memoized, as useCaptionFeed requires.
  const { getToken } = useRoomToken(roomId);
  const { lines } = useCaptionFeed(roomId, viewLang, getToken);
  const { ref, onScroll } = useStickToBottom(lines);

  return (
    <div className="caption-col" ref={ref} onScroll={onScroll}>
      <div className="caption-col-head">
        <h2>{languageName(viewLang)}</h2>
        {languages.length > 1 && (
          <div className="lang-chips">
            {languages.map((lang) => (
              <button
                key={lang}
                type="button"
                className={`lang-chip${lang === viewLang ? " selected" : ""}`}
                aria-pressed={lang === viewLang}
                onClick={() => setViewLang(lang)}
              >
                {languageName(lang)}
              </button>
            ))}
          </div>
        )}
      </div>
      {lines.length === 0 ? (
        <p className="empty">Waiting for translation…</p>
      ) : (
        lines.map((l) => (
          <p key={l.utteranceId} className={l.final ? "line final" : "line partial"}>
            {l.text}
          </p>
        ))
      )}
    </div>
  );
}
