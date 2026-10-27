import type { CreateRoomResponse } from "../lib/captionTypes";
import type { TargetLanguage } from "../lib/languages";
import { languageName } from "../lib/languageHelpers";
import { useStickToBottom } from "../lib/useStickToBottom";
import { RoomPanel } from "./RoomPanel";
import { TranslationChannelColumn } from "./TranslationChannelColumn";
import type { Transcript } from "./useSessionFeed";

// The Session tab: the room panel (QR and join link) while live, then the
// transcript beside the translation.
export function SessionTab({
  room,
  live,
  transcripts,
  translations,
  primaryLanguage,
}: {
  room: CreateRoomResponse | null;
  live: boolean;
  transcripts: Transcript[];
  translations: string[];
  primaryLanguage: TargetLanguage;
}) {
  // Keep each caption column scrolled to the newest line. Both hooks always run
  // (hooks can't be conditional), even when there's a room and the second column
  // isn't shown.
  const { ref: transcriptRef, onScroll: onTranscriptScroll } =
    useStickToBottom(transcripts);
  const { ref: llmRef, onScroll: onLlmScroll } = useStickToBottom(translations);

  return (
    <>
      {room && live && <RoomPanel roomId={room.room_id} languages={room.languages} />}
      <div className="captions">
        <div className="caption-col" ref={transcriptRef} onScroll={onTranscriptScroll}>
          <h2>Transcript</h2>
          {transcripts.length === 0 ? (
            <p className="empty">Waiting for speech…</p>
          ) : (
            transcripts.map((t, i) => (
              <p key={i} className={t.final ? "line final" : "line partial"}>
                {t.text}
              </p>
            ))
          )}
        </div>
        {/* With a room, show the audience's caption channels, so the speaker can
            switch between languages. Without one, show the primary language only. */}
        {room ? (
          <TranslationChannelColumn roomId={room.room_id} languages={room.languages} />
        ) : (
          <div className="caption-col" ref={llmRef} onScroll={onLlmScroll}>
            <h2>{languageName(primaryLanguage)}</h2>
            {translations.length === 0 ? (
              <p className="empty">Waiting for translation…</p>
            ) : (
              translations.map((t, i) => (
                <p key={i} className="line final">
                  {t}
                </p>
              ))
            )}
          </div>
        )}
      </div>
    </>
  );
}
