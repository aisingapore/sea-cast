"""The session history: list, read, zip and delete recorded session folders.

``SessionStore`` works on the folders the recorder writes under ``recordings_dir``,
for the admin history page. A session's id is its folder name (a timestamp, with
a numeric suffix if two sessions started in the same second). Session ids from
requests are checked before they touch the filesystem, so a request can't reach
outside its session's folder.
"""

import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.recording.recorder import AUDIO_FILE, METADATA_FILE, TRANSCRIPT_FILE

_SESSION_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(?:_\d+)?$")

# Fields shown in the list view (the detail view returns everything plus text).
# ``id`` is left out: it always comes from the folder name, which is what the
# routes look sessions up by, not from the metadata.
_SUMMARY_FIELDS = (
    "created_at",
    "ended_at",
    "duration_secs",
    "transcriber",
    "input_language",
    "target_language",
)


def _created_at_from_id(session_id: str) -> str | None:
    """Return the start time encoded in a session id, as an ISO timestamp.

    Args:
        session_id: The session's folder name.

    Returns:
        The timestamp, or None if the id isn't a session id.
    """
    session_id_match = _SESSION_ID_RE.match(session_id)
    if not session_id_match:
        return None
    date, hh, mm, ss = session_id_match.groups()
    return f"{date}T{hh}:{mm}:{ss}"


def _is_plain_file_name(name: object) -> bool:
    """Return whether ``name`` is a bare file name, with no path in it."""
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and "/" not in name
        and "\\" not in name
    )


@dataclass
class SessionStore:
    """Lists, reads, zips and deletes the recorded sessions in ``recordings_dir``.

    Attributes:
        recordings_dir: The folder the recorder writes session folders into.
    """

    recordings_dir: Path

    def list_sessions(self) -> list[dict[str, Any]]:
        """Summarise every session for the list view.

        Returns:
            One summary per session, newest first.
        """
        sessions = [self._summary(d) for d in self._resolve_all(self._entry_names())]
        # The id is the start time (YYYY-MM-DD_HH-MM-SS), so text order is time order.
        sessions.sort(key=lambda s: s["id"], reverse=True)
        return sessions

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return one session's metadata, transcript and translations.

        Args:
            session_id: The session's id.

        Returns:
            The metadata, plus ``transcript`` (lines), ``translations`` (a label
            and lines each) and ``has_audio``. Returns None for an unknown id.
        """
        session_dir = self._resolve(session_id)
        if session_dir is None:
            return None
        metadata = self._read_metadata(session_dir)
        return {
            **metadata,
            "id": session_dir.name,  # the folder name is authoritative
            "transcript": self._read_lines(session_dir / TRANSCRIPT_FILE),
            # metadata.json lists each translation's file. Retrieve their texts.
            "translations": [
                {
                    "label": t.get("label", "Translation"),
                    "lines": self._read_lines(session_dir / t["file"]),
                }
                for t in metadata.get("translations") or []
                if isinstance(t, dict) and _is_plain_file_name(t.get("file"))
            ],
            "has_audio": (session_dir / AUDIO_FILE).is_file(),
        }

    def audio_path(self, session_id: str) -> Path | None:
        """Return the path to a session's audio.wav.

        Args:
            session_id: The session's id.

        Returns:
            The path, or None for an unknown id or a session without audio.
        """
        session_dir = self._resolve(session_id)
        if session_dir is None:
            return None
        audio = session_dir / AUDIO_FILE
        return audio if audio.is_file() else None

    def build_archive(self, session_ids: list[str] | None) -> tuple[Path, list[str]]:
        """Zip sessions into a temporary file.

        Each session's files go under a ``<session-id>/`` folder in the zip. Files
        are streamed into it in chunks, so large audio doesn't use much memory.

        Args:
            session_ids: The sessions to include, or None for all of them.
                Unknown ids are skipped.

        Returns:
            The zip's path and the ids it includes. The caller deletes the file
            once it's been sent.
        """
        if session_ids is None:
            session_ids = sorted(self._entry_names())
        dirs = self._resolve_all(session_ids)

        fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="sea-cast-")
        os.close(fd)  # ZipFile opens the path itself
        tmp_path = Path(tmp_name)
        included: list[str] = []
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for session_dir in dirs:
                    files = [f for f in sorted(session_dir.iterdir()) if f.is_file()]
                    if not files:
                        continue
                    included.append(session_dir.name)
                    for f in files:
                        zf.write(f, arcname=f"{session_dir.name}/{f.name}")
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return tmp_path, included

    def delete_sessions(self, session_ids: list[str]) -> list[str]:
        """Delete sessions' folders; safe to repeat.

        Args:
            session_ids: The sessions to delete. Unknown or already-deleted ids
                are skipped.

        Returns:
            The ids actually deleted.
        """
        deleted: list[str] = []
        for session_dir in self._resolve_all(session_ids):
            shutil.rmtree(session_dir)
            deleted.append(session_dir.name)
        return deleted

    # ------------------------------------------------------------------ helpers

    def _entry_names(self) -> list[str]:
        """Return the name of everything in ``recordings_dir``.

        Not everything in the folder is a folder containing recordings.
        ``_resolve`` skips those files/folders.
        """
        if not self.recordings_dir.is_dir():
            return []
        return [entry.name for entry in self.recordings_dir.iterdir()]

    def _resolve(self, session_id: str) -> Path | None:
        """Return a session's folder, or None for an invalid or unknown id."""
        if not _SESSION_ID_RE.match(session_id):
            return None
        session_dir = self.recordings_dir / session_id
        # Defence in depth: the folder must sit directly under recordings_dir.
        if session_dir.parent != self.recordings_dir or not session_dir.is_dir():
            return None
        return session_dir

    def _resolve_all(self, session_ids: list[str]) -> list[Path]:
        """Return the folders of the given sessions, skipping unknown ids.

        Args:
            session_ids: The session ids. Duplicates are dropped and order is kept.

        Returns:
            The folders of the ids that exist.
        """
        dirs: list[Path] = []
        for session_id in dict.fromkeys(session_ids):
            session_dir = self._resolve(session_id)
            if session_dir is not None:
                dirs.append(session_dir)
        return dirs

    def _read_metadata(self, session_dir: Path) -> dict[str, Any]:
        """Return a session's metadata.json, or a minimal stand-in.

        The stand-in (the id and its start time) covers a session whose
        metadata.json is missing or unreadable (e.g. its first write failed).
        """
        path = session_dir / METADATA_FILE
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    return loaded
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "id": session_dir.name,
            "created_at": _created_at_from_id(session_dir.name),
        }

    def _read_lines(self, path: Path) -> list[str]:
        """Return a text file's lines, or [] if it can't be read."""
        try:
            return path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def _summary(self, session_dir: Path) -> dict[str, Any]:
        """Return one session's list-view summary."""
        metadata = self._read_metadata(session_dir)
        return {
            "id": session_dir.name,
            **{f: metadata[f] for f in _SUMMARY_FIELDS if f in metadata},
            "translators": [
                t.get("label", "Translation")
                for t in metadata.get("translations") or []
                if isinstance(t, dict)
            ],
        }
