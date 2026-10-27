"""Speaker routes: /ws runs a session's pipeline, /stop-session ends it."""

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, WebSocket
from loguru import logger
from pipecat.workers.base_worker import WorkerParams

from app.audio_filters import NoiseFilter, parse_noise_filter
from app.config import SttProfile, TranslatorProfile
from app.delivery.captions import (
    CaptionPublisher,
    build_status_channel,
    build_status_message,
)
from app.languages import AUTO
from app.pipeline import build_pipeline
from app.routes.state import AppState
from app.routes.validation import (
    VALID_INPUT_LANGUAGES,
    VALID_TARGET_LANGUAGES,
    drop_source_language,
)


@dataclass(frozen=True)
class ConnectParams:
    """A speaker session's validated settings, built by parse_connect_params.

    Attributes:
        stt_profile: The STT profile id.
        target_language: The primary target language (the dashboard feed and the
            recorder follow it).
        input_language: The source language code, or "auto".
        translator: The translator profile id.
        vad_silence_secs: Endpoint-silence override, or None for the default
            (the profile's options for ElevenLabs, VAD_STOP_SECS for Silero).
        vad_confidence: Silero confidence override, or None for VAD_CONFIDENCE.
        vad_threshold: ElevenLabs VAD-threshold override, or None for the
            profile's.
        max_segment_secs: Batch-STT forced-flush override, or None for
            STT_MAX_SEGMENT_SECS. 0 turns the flush off.
        noise_filter: "off" or "rnnoise".
        glossary: Whether to use the glossary, when one is configured.
        target_languages: Every target language, primary first (always includes
            ``target_language``).
        room_id: The room to publish captions to ("" for a speaker-only session).
    """

    stt_profile: str
    target_language: str
    input_language: str
    translator: str = ""
    vad_silence_secs: float | None = None
    vad_confidence: float | None = None
    vad_threshold: float | None = None
    max_segment_secs: float | None = None
    noise_filter: NoiseFilter = "off"
    glossary: bool = True
    target_languages: tuple[str, ...] = ()
    room_id: str = ""


def _parse_optional_float(raw: str | None, name: str) -> float | None:
    """Parse an optional number from a query param.

    Args:
        raw: The param's text, or None when it's absent.
        name: The param's name for the error message.

    Returns:
        The number or None when the param is absent or empty (no override).

    Raises:
        ValueError: The text is not a number.
    """
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a number, got {raw!r}") from e


def _parse_bool(raw: str, name: str) -> bool:
    """Parse a true/false query param.

    Args:
        raw: The param's text.
        name: The param's name, for the error message.

    Returns:
        True for "true", False for "false".

    Raises:
        ValueError: Anything else.
    """
    if raw in ("true", "false"):
        return raw == "true"
    raise ValueError(f"{name} must be 'true' or 'false', got {raw!r}")


def parse_connect_params(
    stt_profile: SttProfile,
    target_language: str,
    input_language: str,
    translator: TranslatorProfile | None = None,
    vad_silence_secs: float | None = None,
    vad_confidence: float | None = None,
    vad_threshold: float | None = None,
    max_segment_secs: float | None = None,
    noise_filter: str = "off",
    glossary: bool = True,
    target_languages: str = "",
    room_id: str = "",
    max_target_languages: int = 5,
) -> ConnectParams:
    """Validate a session's settings and build its ConnectParams.

    The target language list is cleaned up. Duplicates are removed,
    ``target_language`` is kept first (it drives the dashboard feed and the
    recorder), and the source language is dropped unless it's the only target,
    since translating a language into itself is wasted work.

    Args:
        stt_profile: The session's STT profile.
        target_language: The primary target language.
        input_language: The source language code, or "auto".
        translator: The session's translator profile.
        vad_silence_secs: Endpoint-silence override (0.3-3.0), or None.
        vad_confidence: Silero confidence override (0.0-1.0), or None.
        vad_threshold: ElevenLabs VAD-threshold override (0.1-0.9), or None.
        max_segment_secs: Batch-STT forced-flush override (0-30, 0 = off), or
            None.
        noise_filter: "off" or "rnnoise".
        glossary: Whether to use the glossary; ignored when none is configured.
        target_languages: Comma-separated fan-out languages. ("" for just
            ``target_language``).
        room_id: The room to publish captions to. ("" for none).
        max_target_languages: The largest number of target languages allowed.

    Returns:
        The validated params. ``target_language`` may differ from the argument
        if it was the source language and another target took its place.

    Raises:
        ValueError: A language is unknown or not supported by the STT profile
            or translator, a VAD value is out of range, the noise filter is
            unknown, there are too many target languages, or several are given
            without a room.
    """
    # Validate languages for the session.
    if target_language not in VALID_TARGET_LANGUAGES:
        raise ValueError(
            f"target_language must be one of {sorted(VALID_TARGET_LANGUAGES)}, got {target_language!r}"
        )
    if input_language not in VALID_INPUT_LANGUAGES:
        raise ValueError(
            f"input_language must be one of {sorted(VALID_INPUT_LANGUAGES)}, got {input_language!r}"
        )
    stt_id = stt_profile.id
    stt_languages = stt_profile.resolved_languages
    if input_language == AUTO:
        if not stt_languages["supports_auto"]:
            raise ValueError(
                f"stt_profile={stt_id!r} does not support auto-detect "
                f"(input_language={AUTO!r})"
            )
    elif stt_languages["auto_only"]:
        raise ValueError(
            f"stt_profile={stt_id!r} auto-detects only. input_language must be {AUTO!r}"
        )
    elif input_language not in stt_languages["recognizes"]:
        raise ValueError(
            f"stt_profile={stt_id!r} cannot recognize input_language={input_language!r}"
        )

    # Validate VAD settings.
    if vad_silence_secs is not None and not 0.3 <= vad_silence_secs <= 3.0:
        raise ValueError(
            f"vad_silence_secs must be in [0.3, 3.0], got {vad_silence_secs}"
        )
    if vad_confidence is not None and not 0.0 <= vad_confidence <= 1.0:
        raise ValueError(f"vad_confidence must be in [0.0, 1.0], got {vad_confidence}")
    if vad_threshold is not None and not 0.1 <= vad_threshold <= 0.9:
        raise ValueError(f"vad_threshold must be in [0.1, 0.9], got {vad_threshold}")
    if max_segment_secs is not None and not 0.0 <= max_segment_secs <= 30.0:
        raise ValueError(f"max_segment_secs must be in [0, 30], got {max_segment_secs}")

    checked_noise_filter = parse_noise_filter(noise_filter)

    # Clean fan-out languages: dedupe, force the primary first, validate, cap.
    langs = [part.strip() for part in target_languages.split(",") if part.strip()]
    if target_language in langs:
        langs.remove(target_language)
    langs.insert(0, target_language)
    langs = list(dict.fromkeys(langs))
    langs = drop_source_language(langs, input_language)
    target_language = langs[0]

    # Check if translator can handle selected languages.
    if translator is not None:
        unsupported = [lang for lang in langs if lang not in translator.translates_to]
        if unsupported:
            raise ValueError(
                f"translator={translator.id!r} cannot translate into {unsupported}"
            )
    unknown_langs = [lang for lang in langs if lang not in VALID_TARGET_LANGUAGES]
    if unknown_langs:
        raise ValueError(
            f"target_languages must be within {sorted(VALID_TARGET_LANGUAGES)}, "
            f"got {unknown_langs}"
        )

    # Cap number of target languages.
    if len(langs) > max_target_languages:
        raise ValueError(
            f"at most {max_target_languages} target languages per session, "
            f"got {len(langs)}"
        )

    # Without a room there is nowhere to deliver the extra translations — each
    # would be a wasted LLM-translation call per utterance.
    if len(langs) > 1 and not room_id:
        raise ValueError(
            "multiple target_languages require a room_id (create one via POST /rooms)"
        )

    return ConnectParams(
        stt_profile=stt_id,
        target_language=target_language,
        input_language=input_language,
        translator=translator.id if translator is not None else "",
        vad_silence_secs=vad_silence_secs,
        vad_confidence=vad_confidence,
        vad_threshold=vad_threshold,
        max_segment_secs=max_segment_secs,
        noise_filter=checked_noise_filter,
        glossary=glossary,
        target_languages=tuple(langs),
        room_id=room_id,
    )


def build_router(state: AppState) -> APIRouter:
    """Return this module's routes, bound to the app's shared state.

    Args:
        state: The app's shared state.

    Returns:
        The router, for ``FastAPI.include_router``.
    """
    settings = state.settings
    room_manager = state.room_manager
    caption_hub = state.caption_hub
    router = APIRouter()

    # ── Room binding for speaker sessions (used by /ws and /stop-session) ──

    async def _validate_room_binding(room_id: str, langs: Sequence[str]) -> None:
        """Pre-flight for a speaker connect that names a room. Raises ValueError."""
        metadata = await room_manager.store.get(room_id)
        if metadata is None:
            raise ValueError(f"room {room_id!r} not found — create it via POST /rooms")
        if metadata.status == "ended":
            raise ValueError(f"room {room_id!r} has already ended")
        # The room's languages were fixed at POST /rooms (they're what the
        # audience was offered); a diverging connect is a client bug.
        if list(langs) != metadata.languages:
            raise ValueError(
                f"target_languages {list(langs)} do not match room {room_id!r} "
                f"languages {metadata.languages}"
            )

    async def _bind_room(
        room_id: str, langs: Sequence[str], stt_profile: str, session_key: str
    ) -> CaptionPublisher:
        """Attach the speaker session to its room and flip it live.

        Returns the publisher the pipeline feeds; audience clients waiting on
        the "created" status see the room go live immediately.
        """
        await room_manager.store.attach_session(
            room_id, session_id=session_key, stt_profile=stt_profile
        )
        publisher = CaptionPublisher(
            caption_hub, room_manager.store, room_id, languages=list(langs)
        )
        await publisher.publish_status("live")
        return publisher

    async def _finish_room(room_id: str) -> None:
        """End the room and notify listeners; the pump closes their sockets.

        Idempotent: only the first call after a live room publishes the
        terminal "ended" status (this runs from both /stop-session and the
        worker's own teardown).
        """
        metadata = await room_manager.store.get(room_id)
        if metadata is None or metadata.status == "ended":
            return
        await room_manager.store.end_room(room_id)
        await caption_hub.publish(
            build_status_channel(room_id),
            build_status_message(
                "ended", metadata.languages, None, sent_at_ms=int(time.time() * 1000)
            ),
        )

    @router.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        """WebSocket transport endpoint.

        Query params: session_id, input_language and target_language (all
        required), stt_profile and translator (empty picks the first profile),
        target_languages, room_id, noise_filter, glossary ("true" by default;
        "false" skips it for this session), plus optional VAD dials
        (vad_silence_secs, vad_confidence, max_segment_secs for the batch
        adapter and vad_silence_secs, vad_threshold for ElevenLabs). Closes with
        code 1008 (Policy Violation) if params are missing or invalid.
        Stays open for the duration of the pipeline. Closing the pipeline
        (via /stop-session?session_id=) or a client disconnect ends it.
        """
        stt_profile = websocket.query_params.get("stt_profile", "")
        target_language = websocket.query_params.get("target_language", "")
        input_language = websocket.query_params.get("input_language", "")
        session_id = websocket.query_params.get("session_id", "")
        translator = websocket.query_params.get("translator", "")
        noise_filter = websocket.query_params.get("noise_filter", "off")

        try:
            for name, value in (
                ("session_id", session_id),
                ("input_language", input_language),
                ("target_language", target_language),
            ):
                if not value:
                    raise ValueError(f"{name} query param is required")
            params = parse_connect_params(
                settings.resolve_stt_profile(stt_profile),
                target_language,
                input_language,
                translator=settings.resolve_translator(translator),
                vad_silence_secs=_parse_optional_float(
                    websocket.query_params.get("vad_silence_secs"), "vad_silence_secs"
                ),
                vad_confidence=_parse_optional_float(
                    websocket.query_params.get("vad_confidence"), "vad_confidence"
                ),
                vad_threshold=_parse_optional_float(
                    websocket.query_params.get("vad_threshold"), "vad_threshold"
                ),
                max_segment_secs=_parse_optional_float(
                    websocket.query_params.get("max_segment_secs"), "max_segment_secs"
                ),
                noise_filter=noise_filter,
                glossary=_parse_bool(
                    websocket.query_params.get("glossary", "true"), "glossary"
                ),
                target_languages=websocket.query_params.get("target_languages", ""),
                room_id=websocket.query_params.get("room_id", ""),
                max_target_languages=settings.max_target_languages,
            )
            if params.room_id:
                await _validate_room_binding(params.room_id, params.target_languages)
        except ValueError as e:
            await websocket.close(code=1008, reason=str(e))
            return

        await websocket.accept()

        publisher: CaptionPublisher | None = None
        if params.room_id:
            publisher = await _bind_room(
                params.room_id, params.target_languages, params.stt_profile, session_id
            )

        session = build_pipeline(
            settings,
            websocket,
            stt_profile=params.stt_profile,
            target_language=params.target_language,
            target_languages=list(params.target_languages),
            input_language=params.input_language,
            translator_profile=params.translator,
            caption_publisher=publisher,
            vad_silence_secs=params.vad_silence_secs,
            vad_confidence=params.vad_confidence,
            vad_threshold=params.vad_threshold,
            max_segment_secs=params.max_segment_secs,
            noise_filter=params.noise_filter,
            use_glossary=params.glossary,
        )
        state.stop_fns[session_id] = session.stop
        session.recorder.start()

        try:
            await session.worker.run(WorkerParams(loop=asyncio.get_running_loop()))
        finally:
            state.stop_fns.pop(session_id, None)
            session.recorder.stop()
            if params.room_id:  # speaker gone ⇒ the room is over
                try:
                    await _finish_room(params.room_id)
                except Exception:
                    logger.exception(f"Failed to end room {params.room_id}")

    @router.post("/stop-session")
    async def stop_session(session_id: str) -> dict[str, str]:
        """Drain in-flight translations for a session before the client disconnects.

        The frontend calls this before closing its socket so the last translation
        is delivered; the 200 response is its signal to close.
        """
        stop_fn = state.stop_fns.pop(session_id, None)
        if stop_fn is None:
            raise HTTPException(status_code=404, detail="Session not found")
        await stop_fn()
        # A roomed session ends its room right after the drain, so the
        # audience's "session ended" doesn't wait on the worker's own
        # teardown (which also calls this — it's idempotent).
        room_id = await room_manager.store.room_for_session(session_id)
        if room_id is not None:
            await _finish_room(room_id)
        return {"status": "ok"}

    return router
