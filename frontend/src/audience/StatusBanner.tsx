// The banner above the captions: "Session ended", or "Reconnecting…" while the
// captions socket retries. Shared by the audience view and the stage monitor.

import type { CaptionsSocketStatus } from "../lib/captionsSocket";

export function StatusBanner({
  connection,
  ended,
}: {
  connection: CaptionsSocketStatus;
  ended: boolean;
}) {
  if (ended) {
    return <div className="aud-banner aud-banner-ended">Session ended</div>;
  }
  if (connection === "reconnecting") {
    return <div className="aud-banner">Reconnecting…</div>;
  }
  return null;
}
