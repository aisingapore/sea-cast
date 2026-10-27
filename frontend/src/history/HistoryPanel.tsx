// Recorded-session browser (admin-only). Moved out of the speaker dashboard
// onto the gated /history route: the list + detail views for GET /sessions*,
// which require an admin token. All backend calls go through the api.ts helpers
// so an expired/invalid token (ApiError 401) is funnelled to `onAuthError`,
// which sends the reader back to the login form.

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  deleteSessions,
  getSession,
  getSessions,
  sessionAudioUrl,
  sessionsDownloadUrl,
} from "../lib/api";
import type { SessionDetail, SessionSummary } from "../lib/captionTypes";

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function formatDuration(secs: number | undefined): string {
  if (secs == null) return "—";
  const total = Math.round(secs);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// Trigger a native browser download (streams to disk via Content-Disposition)
// without navigating the SPA away.
function triggerDownload(url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// Split a recorded translation label ("<translator> · <language>") into its
// parts. Single-language sessions without a room record just "<translator>", so
// their language falls back to the session's primary target. Splits at the last
// separator: a translator's label may contain one, a language never does.
function splitTranslationLabel(
  label: string,
  fallbackLang?: string,
): { translator: string; language: string | undefined } {
  const i = label.lastIndexOf(" · ");
  return i >= 0
    ? { translator: label.slice(0, i), language: label.slice(i + 3) }
    : { translator: label, language: fallbackLang };
}

// From a session's translation labels, derive its translator (one per session),
// the language of each translation in order, and the distinct target languages.
// Shared by the history list and the detail view.
function summarizeTranslations(
  labels: string[] | undefined,
  fallbackLang?: string,
): {
  translator: string | undefined;
  languages: (string | undefined)[];
  targets: string[];
} {
  const parts = (labels ?? []).map((l) => splitTranslationLabel(l, fallbackLang));
  return {
    translator: parts[0]?.translator,
    languages: parts.map((p) => p.language),
    targets: [...new Set(parts.flatMap((p) => (p.language ? [p.language] : [])))],
  };
}

export function HistoryPanel({
  token,
  onAuthError,
}: {
  token: string;
  onAuthError: () => void;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [selected, setSelected] = useState<SessionDetail | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Ids awaiting delete confirmation (null = dialog closed); `deleting` disables
  // the dialog buttons while the request is in flight.
  const [confirming, setConfirming] = useState<string[] | null>(null);
  const [deleting, setDeleting] = useState(false);

  // A 401 means the token lapsed or was revoked — hand back to the login gate
  // rather than showing it as an error. Everything else surfaces with the action
  // that failed (`action` = "load sessions" / "delete sessions" etc.)
  const handleError = useCallback(
    (e: unknown, action: string) => {
      if (e instanceof ApiError && e.status === 401) onAuthError();
      else setError(`Couldn’t ${action}: ${String(e)}`);
    },
    [onAuthError],
  );

  const toggleId = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected =
    sessions.length > 0 && sessions.every((s) => selectedIds.has(s.id));
  const toggleAll = () =>
    setSelectedIds(allSelected ? new Set() : new Set(sessions.map((s) => s.id)));

  const loadList = useCallback(() => {
    getSessions(token)
      .then((list) => {
        setSessions(list);
        setError(null);
      })
      .catch((e) => handleError(e, "load sessions"))
      .finally(() => setLoading(false));
  }, [token, handleError]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  const openSession = useCallback(
    (id: string) => {
      setLoading(true);
      setError(null);
      getSession(token, id)
        .then((detail) => setSelected(detail))
        .catch((e) => handleError(e, "open session"))
        .finally(() => setLoading(false));
    },
    [token, handleError],
  );

  // Delete the confirmed ids, then prune local state so the UI stays in sync
  // without a full reload: drop the rows, clear them from the selection, and
  // close the detail view if the open session was among them.
  const handleConfirmDelete = useCallback(() => {
    if (!confirming) return;
    setDeleting(true);
    deleteSessions(token, confirming)
      .then(({ deleted }) => {
        const gone = new Set(deleted);
        setSessions((prev) => prev.filter((s) => !gone.has(s.id)));
        setSelectedIds((prev) => new Set([...prev].filter((id) => !gone.has(id))));
        setSelected((cur) => (cur && gone.has(cur.id) ? null : cur));
        setConfirming(null);
      })
      .catch((e) => handleError(e, "delete sessions"))
      .finally(() => setDeleting(false));
  }, [token, confirming, handleError]);

  return (
    <>
      {selected ? (
        <HistoryDetail
          key={selected.id}
          token={token}
          session={selected}
          onBack={() => setSelected(null)}
          onDelete={() => setConfirming([selected.id])}
        />
      ) : (
        <div className="history">
          <div className="history-bar">
            <button
              className="btn btn-start"
              onClick={() => {
                setLoading(true);
                setError(null);
                loadList();
              }}
              disabled={loading}
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
            <button
              className="btn btn-start"
              disabled={selectedIds.size === 0}
              onClick={() =>
                triggerDownload(sessionsDownloadUrl(token, [...selectedIds]))
              }
            >
              Download selected ({selectedIds.size})
            </button>
            <button
              className="btn btn-stop"
              disabled={selectedIds.size === 0}
              onClick={() => setConfirming([...selectedIds])}
            >
              Delete selected ({selectedIds.size})
            </button>
          </div>
          {error && <p className="empty">{error}</p>}
          {!error && sessions.length === 0 && (
            <p className="empty">
              {loading ? "Loading…" : "No recorded sessions yet."}
            </p>
          )}
          {sessions.length > 0 && (
            <div className="history-list">
              <div className="history-row history-head">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  aria-label="Select all sessions"
                />
                <div className="history-open">
                  <span>Date / time</span>
                  <span>Transcriber</span>
                  <span>Translator</span>
                  <span>Target</span>
                  <span>Duration</span>
                </div>
              </div>
              {sessions.map((s) => {
                const { translator, targets } = summarizeTranslations(
                  s.translators,
                  s.target_language,
                );
                return (
                  <div key={s.id} className="history-row">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(s.id)}
                      onChange={() => toggleId(s.id)}
                      aria-label={`Select session ${s.id}`}
                    />
                    <button className="history-open" onClick={() => openSession(s.id)}>
                      <span>{formatDateTime(s.created_at)}</span>
                      <span>{s.transcriber ?? "—"}</span>
                      <span>{translator ?? "—"}</span>
                      <span>{targets.join(", ") || s.target_language || "—"}</span>
                      <span>{formatDuration(s.duration_secs)}</span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
      {confirming && (
        <ConfirmDialog
          count={confirming.length}
          busy={deleting}
          onCancel={() => setConfirming(null)}
          onConfirm={handleConfirmDelete}
        />
      )}
    </>
  );
}

// Destructive-action guard shared by the list ("Delete selected") and the
// detail view ("Delete"). A dimmed full-screen overlay; the confirm button uses
// the danger style (.btn-stop).
function ConfirmDialog({
  count,
  busy,
  onCancel,
  onConfirm,
}: {
  count: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="confirm-overlay" role="dialog" aria-modal="true">
      <div className="confirm-box">
        <h2>
          Delete {count} session{count === 1 ? "" : "s"}?
        </h2>
        <p>
          This permanently removes their transcripts, audio, and translations. This
          cannot be undone.
        </p>
        <div className="confirm-actions">
          <button className="btn btn-start" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-stop" onClick={onConfirm} disabled={busy}>
            {busy ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

function HistoryDetail({
  token,
  session,
  onBack,
  onDelete,
}: {
  token: string;
  session: SessionDetail;
  onBack: () => void;
  onDelete: () => void;
}) {
  // A session can list several translations (one per target language). Show the
  // transcript and one translation by default and let the reader open the rest,
  // so the view isn't crowded. Keyed by session id at the call site, so this
  // resets when a different session is opened.
  const [shown, setShown] = useState<Set<number>>(() => new Set([0]));
  const toggle = (i: number) =>
    setShown((cur) => {
      const next = new Set(cur);
      if (next.has(i)) {
        if (next.size > 1) next.delete(i); // keep at least one open
      } else {
        next.add(i);
      }
      return next;
    });
  const cols = 1 + session.translations.filter((_, i) => shown.has(i)).length;
  // Show the translator once, and label each translation by its language.
  const { translator, languages, targets } = summarizeTranslations(
    session.translations.map((t) => t.label),
    session.target_language,
  );
  const labelFor = (ti: number) => languages[ti] ?? session.translations[ti].label;
  return (
    <div className="history-detail">
      <div className="history-bar">
        <button className="btn btn-start" onClick={onBack}>
          ← Back
        </button>
        <button
          className="btn btn-start"
          onClick={() => triggerDownload(sessionsDownloadUrl(token, [session.id]))}
        >
          Download
        </button>
        <button className="btn btn-stop" onClick={onDelete}>
          Delete
        </button>
      </div>
      <dl className="history-meta">
        <div>
          <dt>When</dt>
          <dd>{formatDateTime(session.created_at)}</dd>
        </div>
        <div>
          <dt>Transcriber</dt>
          <dd>{session.transcriber ?? "—"}</dd>
        </div>
        <div>
          <dt>Translator</dt>
          <dd>{translator ?? "—"}</dd>
        </div>
        <div>
          <dt>Spoken → Targets</dt>
          <dd>
            {(session.input_language ?? "—") +
              " → " +
              (targets.join(", ") || session.target_language || "—")}
          </dd>
        </div>
        <div>
          <dt>Duration</dt>
          <dd>{formatDuration(session.duration_secs)}</dd>
        </div>
      </dl>
      {session.has_audio && (
        <audio
          className="history-audio"
          controls
          src={sessionAudioUrl(token, session.id)}
        />
      )}
      {session.translations.length > 1 && (
        <div className="history-tr-toggle">
          <span>Translations</span>
          <div className="lang-chips">
            {session.translations.map((_, ti) => (
              <button
                key={ti}
                type="button"
                className={`lang-chip${shown.has(ti) ? " selected" : ""}`}
                aria-pressed={shown.has(ti)}
                onClick={() => toggle(ti)}
              >
                {labelFor(ti)}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="captions" style={{ gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
        <div className="caption-col">
          <h2>Transcript</h2>
          {session.transcript.length === 0 ? (
            <p className="empty">No transcript.</p>
          ) : (
            session.transcript.map((t, i) => (
              <p key={i} className="line final">
                {t}
              </p>
            ))
          )}
        </div>
        {session.translations.map((tr, ti) =>
          shown.has(ti) ? (
            <div className="caption-col" key={ti}>
              <h2>{labelFor(ti)}</h2>
              {tr.lines.length === 0 ? (
                <p className="empty">No translation.</p>
              ) : (
                tr.lines.map((l, i) => (
                  <p key={i} className="line final">
                    {l}
                  </p>
                ))
              )}
            </div>
          ) : null,
        )}
      </div>
    </div>
  );
}
