"""Session recording: one timestamped folder per session.

Each folder holds:

- ``transcript.txt``: the final transcripts, one per line.
- ``translation.{lang}.txt``: one file per target language.
- ``glossary.{lang}.jsonl``: when the session uses a glossary, one record per
  utterance per language (the terms found, the block sent and the result).
- ``audio.wav``: the speaker's audio.
- ``metadata.json``: the transcriber, translators, languages and timing, plus a
  per-language latency summary when there is one.

app.pipeline feeds these methods from the pipeline, and app.recording.history
reads the folders back for the history page.
"""

import contextlib
import json
import os
import wave
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from loguru import logger

TRANSCRIPT_FILE = "transcript.txt"
AUDIO_FILE = "audio.wav"
METADATA_FILE = "metadata.json"


def _now() -> datetime:
    """Return the local time (a function so tests can replace it)."""
    return datetime.now()


class SessionRecorder:
    """Records one session into its own folder (see the module docstring).

    Call ``start`` to create the folder, the ``on_*`` methods during the session,
    then ``stop`` to write the audio and the final metadata. The metadata given
    up front is written at ``start``. ``stop`` adds the timing, the transcript
    count and the latency summary from ``set_metrics_source``.
    """

    def __init__(
        self, recordings_dir: Path, metadata: dict[str, Any] | None = None
    ) -> None:
        """Create a recorder.

        Args:
            recordings_dir: The folder each session's folder is created in.
            metadata: The session facts known up front, written to
                ``metadata.json``.
        """
        self._recordings_dir = recordings_dir
        self._metadata: dict[str, Any] = dict(metadata or {})
        self._session_dir: Path | None = None
        self._transcript_handle: IO[str] | None = None
        # One open file handle per translation or glossary file, keyed by filename.
        self._line_handles: dict[str, IO[str]] = {}
        self._transcript_lines: int = 0
        self._audio_chunks: list[bytes] = []
        self._sample_rate: int = 16000
        self._channels: int = 1
        self._started_at: datetime | None = None
        self._started: bool = False
        self._stopped: bool = False
        # Optional latency-summary provider, pulled once at stop().
        self._metrics_source: Callable[[], dict[str, Any]] | None = None
        self._metrics: dict[str, Any] | None = None

    def set_metrics_source(self, source: Callable[[], dict[str, Any]]) -> None:
        """Set where the latency summary comes from; it's read once, at ``stop``.

        Args:
            source: Returns the summary, written under ``metrics`` in
                ``metadata.json``.
        """
        self._metrics_source = source

    def start(self, *, sample_rate: int = 16000, channels: int = 1) -> None:
        """Create the session folder, open the transcript file and write metadata.

        The folder is named after the current time, with a numeric suffix if
        another session started in the same second.

        Args:
            sample_rate: The audio's sample rate, for audio.wav.
            channels: The audio's channel count, for audio.wav.
        """
        timestamp = _now().strftime("%Y-%m-%d_%H-%M-%S")
        # Create the folder atomically. On a same-second collision (two concurrent
        # sessions), disambiguate with a numeric suffix to avoid conflicts.
        session_dir = self._recordings_dir / timestamp
        suffix = 1
        while True:
            try:
                session_dir.mkdir(parents=True)
                break
            except FileExistsError:
                session_dir = self._recordings_dir / f"{timestamp}_{suffix}"
                suffix += 1
        self._session_dir = session_dir
        self._transcript_handle = open(  # noqa: SIM115
            session_dir / TRANSCRIPT_FILE, "w", encoding="utf-8"
        )
        self._line_handles = {}
        self._transcript_lines = 0
        self._audio_chunks = []
        self._sample_rate = sample_rate
        self._channels = channels
        self._started_at = _now()
        self._started = True
        self._stopped = False
        self._write_metadata(final=False)

    def on_transcript(self, text: str) -> None:
        """Append a final transcript line to the transcript file.

        Args:
            text: The final transcript.
        """
        self._require_active("on_transcript")
        assert self._transcript_handle is not None
        self._transcript_handle.write(text + "\n")
        self._transcript_handle.flush()
        self._transcript_lines += 1

    def on_translation(self, text: str, filename: str) -> None:
        """Append a translation line to a translation file.

        Args:
            text: The translation.
            filename: The language's file, translation.<lang>.txt; it's created on
                first use.
        """
        self._require_active("on_translation")
        self._append_line(filename, text)

    def on_glossary(self, record: dict[str, Any], filename: str) -> None:
        """Append one utterance's glossary record, as a JSON line.

        Args:
            record: The record from app.translation.terminology.build_term_record.
            filename: The language's file, glossary.<lang>.jsonl, created on
                first use.
        """
        self._require_active("on_glossary")
        self._append_line(filename, json.dumps(record, ensure_ascii=False))

    def _append_line(self, filename: str, line: str) -> None:
        """Write one line to a session file, opening it on first use.

        Args:
            filename: A file inside the session folder.
            line: The text to write (with a newline appended).
        """
        handle = self._line_handles.get(filename)
        if handle is None:
            assert self._session_dir is not None
            handle = open(  # noqa: SIM115
                self._session_dir / filename, "w", encoding="utf-8"
            )
            self._line_handles[filename] = handle
        handle.write(line + "\n")
        handle.flush()

    def on_audio(self, data: bytes) -> None:
        """Buffer a chunk of the speaker's audio, written to audio.wav at ``stop``.

        Does nothing before ``start`` or after ``stop``, since audio can arrive
        around the edges of a session.

        Args:
            data: Raw 16-bit PCM.
        """
        if not self._started or self._stopped:
            return
        self._audio_chunks.append(data)

    def stop(self) -> None:
        """Close the files, then write audio.wav and the final metadata.

        Safe to call more than once, or without ``start``.
        """
        if not self._started or self._stopped:
            return
        self._stopped = True
        # Read the latency summary before writing the final metadata. If the
        # metrics source fails, or returns something JSON can't encode, the
        # summary is left out so the rest of the session is still saved.
        if self._metrics_source is not None:
            try:
                metrics = self._metrics_source()
                json.dumps(metrics)
                self._metrics = metrics
            except Exception:
                logger.exception(
                    "Latency summary failed. Saving the session without it..."
                )
                self._metrics = None
        if self._transcript_handle is not None:
            self._transcript_handle.close()
            self._transcript_handle = None
        for handle in self._line_handles.values():
            handle.close()
        self._line_handles = {}
        self._flush_audio()
        self._write_metadata(final=True)

    # ------------------------------------------------------------------ helpers

    def _require_active(self, method: str) -> None:
        """Fail unless the recorder is between ``start`` and ``stop``.

        Args:
            method: The calling method, for the error message.

        Raises:
            RuntimeError: Called before ``start`` or after ``stop``.
        """
        if not self._started:
            raise RuntimeError(f"SessionRecorder.{method} called before start()")
        if self._stopped:
            raise RuntimeError(f"SessionRecorder.{method} called after stop()")

    def _flush_audio(self) -> None:
        """Write the buffered audio to audio.wav."""
        assert self._session_dir is not None
        pcm = b"".join(self._audio_chunks)
        with wave.open(str(self._session_dir / AUDIO_FILE), "wb") as wf:
            wf.setnchannels(self._channels)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm)
        self._audio_chunks = []

    def _write_metadata(self, *, final: bool) -> None:
        """Write metadata.json. A write error is logged and never raised.

        Args:
            final: Whether to add the end time, duration, transcript count and
                latency summary (at ``stop``).
        """
        if self._session_dir is None or self._started_at is None:
            return
        metadata = dict(self._metadata)
        metadata["id"] = self._session_dir.name
        metadata["created_at"] = self._started_at.isoformat(timespec="seconds")
        metadata["transcript_file"] = TRANSCRIPT_FILE
        metadata["audio_file"] = AUDIO_FILE
        if final:
            ended = _now()
            metadata["ended_at"] = ended.isoformat(timespec="seconds")
            metadata["duration_secs"] = round(
                (ended - self._started_at).total_seconds(), 1
            )
            metadata["transcript_lines"] = self._transcript_lines
            if self._metrics:
                metadata["metrics"] = self._metrics
        path = self._session_dir / METADATA_FILE
        tmp_path = path.with_name(METADATA_FILE + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except OSError:
            logger.exception("Could not write {}", path)
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)
