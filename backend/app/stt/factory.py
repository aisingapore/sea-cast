"""Build the STT service for a session from its STT profile.

Each profile in ``sea-cast.yaml`` names an adapter, and the adapter decides which
service is built:

- ``openai`` (batch): ``OpenAICompatibleBatchSTT`` (``app/stt/batch/openai.py``),
  for any OpenAI-compatible ``/audio/transcriptions`` endpoint (whisper.cpp,
  vLLM, OpenRouter, ...). Silero segments the audio upstream.
- ``elevenlabs`` (realtime): ``ElevenLabsRealtimeSTT``
  (``app/stt/realtime/elevenlabs.py``), a persistent WebSocket that streams audio
  and commits transcripts with ElevenLabs' server-side VAD.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, assert_never

from loguru import logger
from pipecat.transcriptions.language import Language

from app.config import (
    BasicAuth,
    BearerAuth,
    ElevenLabsSttProfile,
    OpenAISttProfile,
    SttProfile,
)
from app.languages import AUTO

if TYPE_CHECKING:
    from pipecat.services.stt_service import STTService


def _resolve_source_language(profile: SttProfile, code: str) -> Language | None:
    """Resolve a source language code to the Pipecat Language for a profile.

    Applies the profile's language code overrides first (e.g. Whisper's
    ``fil`` → ``tl``).

    Args:
        profile: The session's STT profile.
        code: The session's source language code, or "auto".

    Returns:
        The Pipecat Language, or None for "auto" or a code Pipecat has no member
        for (e.g. Hokkien ``nan``). The batch adapter then falls back to English.
    """
    if code == AUTO:
        return None
    overrides = profile.resolved_languages["code_overrides"]
    try:
        return Language(overrides.get(code, code))
    except ValueError:
        return None


def _build_openai_stt(profile: OpenAISttProfile, language: str) -> STTService:
    """Build a batch STT for one OpenAI-compatible endpoint.

    Args:
        profile: The ``openai`` profile.
        language: The session's source language code; "auto" and codes Pipecat
            can't express fall back to English.

    Returns:
        The configured OpenAICompatibleBatchSTT.
    """
    from pipecat.services.openai.stt import OpenAISTTSettings

    from app.stt.batch.openai import OpenAICompatibleBatchSTT

    lang = _resolve_source_language(profile, language) or Language.EN
    auth = profile.auth

    api_key = username = password = None
    if isinstance(auth, BearerAuth):
        api_key = os.environ.get(auth.api_key_env)
    elif isinstance(auth, BasicAuth):
        username = os.environ.get(auth.username_env)
        password = os.environ.get(auth.password_env)

    logger.info(
        "STT: {} (adapter=openai, base_url={}, model={}, language={}, auth={})",
        profile.id,
        profile.base_url,
        profile.model,
        lang,
        auth.type if auth else "none",
    )

    return OpenAICompatibleBatchSTT(
        profile_id=profile.id,
        profile_label=profile.label,
        base_url=profile.base_url,
        api_key=api_key,
        username=username,
        password=password,
        timeout_secs=profile.options.timeout_secs,
        settings=OpenAISTTSettings(model=profile.model, language=lang),
    )


def _build_elevenlabs_stt(
    profile: ElevenLabsSttProfile,
    language: str,
    *,
    vad_silence_secs: float | None,
    vad_threshold: float | None,
) -> STTService:
    """Build the ElevenLabs realtime STT.

    Args:
        profile: The ``elevenlabs`` profile.
        language: The session's source language code; "auto" lets ElevenLabs
            auto-detect (it handles code-switching).
        vad_silence_secs: Per-session override of the profile's commit silence.
        vad_threshold: Per-session override of the profile's VAD threshold.

    Returns:
        The configured ElevenLabsRealtimeSTT.
    """
    from pipecat.services.elevenlabs.stt import (
        CommitStrategy,
        ElevenLabsRealtimeSTTSettings,
    )

    from app.stt.realtime.elevenlabs import ElevenLabsRealtimeSTT

    lang = _resolve_source_language(profile, language)

    if vad_silence_secs is None:
        vad_silence_secs = profile.options.vad_silence_secs

    if vad_threshold is None:
        vad_threshold = profile.options.vad_threshold

    # If lang is None, ElevenLabs will auto-detect.
    stt_settings = ElevenLabsRealtimeSTTSettings(
        model=profile.model,
        language=lang,
        vad_silence_threshold_secs=vad_silence_secs,
        vad_threshold=vad_threshold,
    )
    strategy = CommitStrategy.VAD
    logger.info(
        "STT: {} (adapter=elevenlabs, base_url={}, model={}, language={}, "
        "commit={}, vad_silence={}s, vad_threshold={})",
        profile.id,
        profile.base_url,
        profile.model,
        lang or "auto-detect",
        strategy.name,
        vad_silence_secs,
        vad_threshold,
    )

    return ElevenLabsRealtimeSTT(
        api_key=os.environ.get(profile.auth.api_key_env, ""),
        base_url=profile.base_url,
        commit_strategy=strategy,
        settings=stt_settings,
    )


def make_stt(
    profile: SttProfile,
    language: str,
    *,
    vad_silence_secs: float | None = None,
    vad_threshold: float | None = None,
) -> STTService:
    """Return the STT service for a session.

    Args:
        profile: The session's STT profile.
        language: The session's source language code (or "auto").
        vad_silence_secs: Per-session ElevenLabs commit-silence override.
        vad_threshold: Per-session ElevenLabs VAD-threshold override.

    Returns:
        The configured Pipecat STT service.
    """
    if isinstance(profile, OpenAISttProfile):
        return _build_openai_stt(profile, language)
    if isinstance(profile, ElevenLabsSttProfile):
        return _build_elevenlabs_stt(
            profile,
            language,
            vad_silence_secs=vad_silence_secs,
            vad_threshold=vad_threshold,
        )
    # An adapter added to SttProfile without a branch here will raise an error.
    assert_never(profile)
