// Scrolling caption feed: finals opaque, the trailing partial dimmed. Shared
// by the audience view and the stage monitor (which restyles via .monitor).

import type { CaptionLine } from "../lib/captionFeed";
import { useStickToBottom } from "../lib/useStickToBottom";

export function CaptionTicker({ lines }: { lines: CaptionLine[] }) {
  const { ref, onScroll, atBottom, scrollToBottom } = useStickToBottom(lines);
  return (
    <div className="ticker-wrap">
      <div className="ticker" ref={ref} onScroll={onScroll}>
        {lines.map((line) => (
          <p
            key={line.utteranceId}
            className={line.final ? "ticker-line" : "ticker-line partial"}
          >
            {line.text}
          </p>
        ))}
      </div>
      {/* Shown only while the user has scrolled up; taps back to the live edge. */}
      {!atBottom && (
        <button
          type="button"
          className="ticker-jump"
          onClick={scrollToBottom}
          aria-label="Jump to latest captions"
        >
          ↓ Latest
        </button>
      )}
    </div>
  );
}
