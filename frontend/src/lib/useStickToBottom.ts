import { useCallback, useLayoutEffect, useRef, useState } from "react";

// Distance from the bottom (px) within which we still count as "stuck" and keep
// auto-scrolling. Generous enough to survive sub-pixel rounding and a tap of
// touch jitter, tight enough that a deliberate scroll-up unsticks immediately.
const STICK_THRESHOLD = 32;

// Auto-scroll a caption column to the newest line, but yield to a user who has
// scrolled up to read back. `dep` should be the rendered array (recreated on every
// streaming update) so the effect fires on each new/changed line.
//
// `atBottom` drives a "jump to latest" affordance: it's false while the user is
// reading back, true while we're following the live edge. `scrollToBottom` snaps
// back to the newest line and re-sticks.
export function useStickToBottom(dep: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // Stick while near the bottom; pause auto-scroll once the user scrolls away.
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD);
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  // useLayoutEffect (not useEffect) so we scroll before the browser paints the
  // new line — avoids a one-frame flash of the pre-scroll position.
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && atBottom) el.scrollTop = el.scrollHeight;
  }, [dep, atBottom]);

  return { ref, onScroll, atBottom, scrollToBottom };
}
