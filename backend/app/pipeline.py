"""Pipeline assembly for one speaker session over the WebSocket transport.

Creates the full Pipecat pipeline. Its shape depends on whether the STT profile is
batch or streaming:

Batch STT (the ``openai`` adapter):
  transport.input → AudioBuffer → VADProcessor → [MaxSegmentFlusher]
    → STT → translator → transport.output

Streaming STT (the ``elevenlabs`` adapter):
  transport.input → AudioBuffer → VADProcessor
    → STT → translator → transport.output

Silero VAD runs on both paths so the STT can report TTFB. For batch adapters it
also segments the audio. For streaming adapters it is metrics-only — ElevenLabs
commits via its own server-side VAD and VADProcessor forwards all audio.
The MaxSegmentFlusher is batch-only.

With a room or several target languages, ``FanOutTranslator`` replaces
``LLMTranslator`` and translates into every language at once.

The recorder gets each final transcript from ``_TranscriptObserver``, and each
translation from the translator's ``on_translation`` callback.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from fastapi import WebSocket
from loguru import logger
from openai import AsyncOpenAI
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app.audio_filters import NoiseFilter, make_audio_in_filter
from app.config import Settings, TranslatorProfile
from app.delivery.captions import CaptionPublisher
from app.recording.recorder import SessionRecorder
from app.stt import make_stt
from app.translation.fanout import FanOutTranslator
from app.translation.llm import make_translator
from app.translation.terminology import TermProvider, make_term_provider


class _MaxSegmentFlusher(FrameProcessor):
    """Forces a VAD stop+start pair downstream when speech exceeds max_segment_secs.

    Prevents SegmentedSTTService from buffering indefinitely when a speaker never
    pauses long enough to trigger VAD silence detection.
    """

    def __init__(self, *, max_segment_secs: float, vad_params: VADParams) -> None:
        """Set the segment limit and the VAD timings the synthetic frames carry.

        Args:
            max_segment_secs: The longest continuous speech before a forced flush.
            vad_params: The session's Silero VAD params; their start/stop times are
                copied into the synthetic frames so they look like Silero's own.
        """
        super().__init__()
        self._max_segment_secs = max_segment_secs
        self._vad_params = vad_params
        self._speaking = False
        self._accumulated_secs: float = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass every frame through, counting speech audio and flushing at the limit.

        Args:
            frame: The incoming frame.
            direction: The direction the frame is travelling in the pipeline.
        """
        await super().process_frame(frame, direction)

        # Forward Silero VAD frames as usual.
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._speaking = True
            self._accumulated_secs = 0.0
            await self.push_frame(frame, direction)
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._speaking = False
            self._accumulated_secs = 0.0
            await self.push_frame(frame, direction)

        # Artificially add stop/start frames when segment limit is reached.
        elif isinstance(frame, AudioRawFrame) and self._speaking:
            await self.push_frame(frame, direction)
            # PCM is 16-bit (2 bytes/sample), so duration = bytes / (rate * 2).
            self._accumulated_secs += len(frame.audio) / (frame.sample_rate * 2)
            if self._accumulated_secs >= self._max_segment_secs:
                self._accumulated_secs = 0.0
                await self.push_frame(
                    VADUserStoppedSpeakingFrame(stop_secs=self._vad_params.stop_secs),
                    FrameDirection.DOWNSTREAM,
                )
                await self.push_frame(
                    VADUserStartedSpeakingFrame(start_secs=self._vad_params.start_secs),
                    FrameDirection.DOWNSTREAM,
                )
        else:
            await self.push_frame(frame, direction)


class _TranslationDrain:
    """Counts in-flight translations so stopping a session can wait for them.

    Incremented when STT commits a transcript (observed at the STT push, which
    happens before the translator dequeues it, so queued-but-not-yet-translated
    transcripts are counted too). Decremented when the translator finishes that
    unit. asyncio is single-threaded, so the plain int needs no lock.
    """

    def __init__(self) -> None:
        """Start with nothing in flight."""
        self._pending = 0
        self._idle = asyncio.Event()
        self._idle.set()

    def increment(self) -> None:
        """Count one more transcript waiting to be translated."""
        self._pending += 1
        self._idle.clear()

    def decrement(self) -> None:
        """Count one translation as finished.

        When none are left, ``wait_until_idle`` returns.
        """
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self._idle.set()

    async def wait_until_idle(self, timeout_secs: float) -> bool:
        """Wait until no translations are in flight, or the timeout passes.

        Args:
            timeout_secs: The longest to wait.

        Returns:
            True if every translation finished, False on timeout.
        """
        try:
            async with asyncio.timeout(timeout_secs):
                await self._idle.wait()
            return True
        except TimeoutError:
            return False


class _TranscriptObserver(BaseObserver):
    """Records each final transcript and counts it as pending translation."""

    def __init__(
        self,
        recorder: SessionRecorder,
        stt: FrameProcessor,
        drain: _TranslationDrain,
    ) -> None:
        """Holds the recorder, the STT to watch, and the drain to count into.

        Args:
            recorder: Writes the session's transcript file.
            stt: The pipeline's STT service.
            drain: Counts transcripts still waiting to be translated.
        """
        super().__init__()
        self._recorder = recorder
        self._stt = stt
        self._drain = drain

    async def on_push_frame(self, data: FramePushed) -> None:
        """Record a non-empty transcript from the STT and count it in the drain.

        Args:
            data: The pushed frame, with the processor that pushed it.
        """
        if data.source is not self._stt:
            return
        frame = data.frame
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            self._recorder.on_transcript(frame.text)
            # Mark this committed transcript as in-flight translation work so a
            # WebSocket stop can drain it before the client closes the socket.
            self._drain.increment()


def _translation_file(lang: str) -> str:
    """Return the recording file name for one language's translations."""
    return f"translation.{lang.lower()}.txt"


def _glossary_file(lang: str) -> str:
    """Return the recording file name for one language's glossary records."""
    return f"glossary.{lang.lower()}.jsonl"


@dataclass(frozen=True)
class SessionPipeline:
    """One session's assembled pipeline.

    Attributes:
        worker: Runs the pipeline.
        recorder: Records the session; the caller starts and stops it.
        stop: Await it before the client closes the socket, so in-flight
            translations can finish.
    """

    worker: PipelineWorker
    recorder: SessionRecorder
    stop: Callable[[], Awaitable[None]]


def _build_recorder(
    settings: Settings,
    *,
    stt_label: str,
    input_language: str,
    targets: Sequence[str],
    translator_label: str,
    use_fanout: bool,
    noise_filter: NoiseFilter,
    glossary: bool,
) -> SessionRecorder:
    """Create the session's recorder, with the metadata known up front.

    Args:
        settings: The app settings.
        stt_label: The STT profile's label.
        input_language: The source language code, or "auto".
        targets: Every target language, primary first.
        translator_label: The translator profile's label.
        use_fanout: Whether the session uses the fan-out translator.
        noise_filter: The resolved noise filter.
        glossary: Whether the session uses a glossary.

    Returns:
        The recorder.
    """
    if use_fanout:
        translations = [
            {"label": f"{translator_label} · {lang}", "file": _translation_file(lang)}
            for lang in targets
        ]
    else:
        translations = [
            {"label": translator_label, "file": _translation_file(targets[0])}
        ]
    metadata = {
        "transcriber": stt_label,
        "input_language": input_language,
        "target_language": targets[0],
        "noise_filter": noise_filter,
        "glossary": glossary,
        "translations": translations,
    }
    return SessionRecorder(settings.recordings_dir, metadata)


def _build_transport(
    settings: Settings, websocket: WebSocket, noise_filter: NoiseFilter
) -> FastAPIWebsocketTransport:
    """Create the WebSocket transport for the speaker's connection.

    Args:
        settings: The app settings.
        websocket: The speaker's dashboard connection.
        noise_filter: The resolved noise filter for incoming audio.

    Returns:
        The transport.
    """
    return FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_filter=make_audio_in_filter(noise_filter),
            add_wav_header=False,
            session_timeout=settings.ws_session_timeout_secs,
            serializer=ProtobufFrameSerializer(
                params=FrameSerializer.InputParams(ignore_rtvi_messages=False)
            ),
        ),
    )


def _build_vad(
    settings: Settings,
    *,
    is_batch_stt: bool,
    vad_silence_secs: float | None,
    vad_confidence: float | None,
    max_segment_secs: float | None,
) -> tuple[VADProcessor, _MaxSegmentFlusher | None]:
    """Create the Silero VAD and, for batch STT, the max-segment flusher.

    Args:
        settings: The app settings.
        is_batch_stt: Whether the STT profile is batch (Silero segments the
            audio) rather than streaming.
        vad_silence_secs: Per-session endpoint silence. Used as Silero's
            stop_secs for batch STT only; for streaming STT, Silero keeps
            ``vad_stop_secs`` and this goes to the STT's own VAD instead.
        vad_confidence: Per-session Silero confidence.
        max_segment_secs: Per-session forced-flush limit, or None for
            ``stt_max_segment_secs``.

    Returns:
        The VAD processor, and the flusher (None for streaming STT, or when the
        limit is 0).
    """
    if max_segment_secs is None:
        max_segment_secs = settings.stt_max_segment_secs
    vad_params = VADParams(
        stop_secs=(
            vad_silence_secs
            if vad_silence_secs is not None and is_batch_stt
            else settings.vad_stop_secs
        ),
        start_secs=settings.vad_start_secs,
        confidence=(
            vad_confidence if vad_confidence is not None else settings.vad_confidence
        ),
        min_volume=settings.vad_min_volume,
    )
    vad_processor = VADProcessor(vad_analyzer=SileroVADAnalyzer(params=vad_params))
    # The flusher forces a flush when a speaker never pauses. Only batch STT
    # needs it, streaming STT segments server-side.
    segment_flusher = (
        _MaxSegmentFlusher(max_segment_secs=max_segment_secs, vad_params=vad_params)
        if is_batch_stt and max_segment_secs > 0
        else None
    )
    return vad_processor, segment_flusher


def _build_translator(
    settings: Settings,
    profile: TranslatorProfile,
    *,
    targets: Sequence[str],
    use_fanout: bool,
    caption_publisher: CaptionPublisher | None,
    term_provider: TermProvider | None,
    drain: _TranslationDrain,
    recorder: SessionRecorder,
) -> FrameProcessor:
    """Create the translation stage, recording into ``recorder``.

    Args:
        settings: The app settings.
        profile: The translator profile.
        targets: Every target language, primary first.
        use_fanout: Whether to translate with FanOutTranslator rather than one
            LLMTranslator.
        caption_publisher: Publishes captions to the room, or None.
        term_provider: The glossary's term provider, or None.
        drain: Counts each finished translation.
        recorder: Records each translation and glossary record.

    Returns:
        The FanOutTranslator or LLMTranslator.
    """
    client = AsyncOpenAI(
        base_url=profile.base_url,
        api_key=settings.resolve_translator_api_key(profile),
    )

    def record_translation(lang: str, text: str) -> None:
        """Write one translation to the target language's file."""
        recorder.on_translation(text, _translation_file(lang))

    def record_glossary(lang: str, record: dict[str, object]) -> None:
        """Write one glossary record to the target language's file."""
        recorder.on_glossary(record, _glossary_file(lang))

    if use_fanout:
        fanout = FanOutTranslator(
            client=client,
            model=profile.model,
            target_languages=targets,
            system_prompt_template=settings.load_system_prompt(),
            rolling_window_n=settings.rolling_window_n,
            publisher=caption_publisher,
            extra_body=profile.extra_body,
            on_unit_done=drain.decrement,
            on_translation=record_translation,
            processor_label=profile.label,
            term_provider=term_provider,
            on_glossary=record_glossary,
        )
        # Add the per-language latency summary into metadata.json at stop().
        recorder.set_metrics_source(fanout.metrics_summary)
        return fanout
    return make_translator(
        settings,
        client,
        targets[0],
        profile.model,
        extra_body=profile.extra_body,
        on_unit_done=drain.decrement,
        on_translation=record_translation,
        term_provider=term_provider,
        on_glossary=record_glossary,
    )


def _build_audio_buffer(recorder: SessionRecorder) -> AudioBufferProcessor:
    """Create the buffer that hands the speaker's audio to the recorder.

    Args:
        recorder: Records the session's audio.

    Returns:
        The audio buffer.
    """
    audio_buffer = AudioBufferProcessor(num_channels=1, sample_rate=16000)

    @audio_buffer.event_handler("on_audio_data")
    async def _on_audio(
        proc: AudioBufferProcessor,
        audio: bytes,
        sample_rate: int,
        num_channels: int,
    ) -> None:
        recorder.on_audio(audio)

    return audio_buffer


def _add_session_handlers(
    settings: Settings,
    transport: FastAPIWebsocketTransport,
    audio_buffer: AudioBufferProcessor,
    worker: PipelineWorker,
    drain: _TranslationDrain,
) -> Callable[[], Awaitable[None]]:
    """Tie recording and the worker to the client's connection.

    Args:
        settings: The app settings.
        transport: The speaker's transport.
        audio_buffer: Records the speaker's audio.
        worker: Runs the pipeline; cancelled when the client disconnects.
        drain: Counts translations still in flight.

    Returns:
        ``stop_session``.
    """

    async def stop_session() -> None:
        """Let in-flight translations finish before the client closes the socket.

        The dashboard's Pipecat client (its WebSocket transport) treats a
        server-initiated close as a *fatal* error, so stop_session must NOT end
        the pipeline itself. It lets in-flight translations finish streaming to
        the client over the still-open socket, then returns. The /stop-session
        HTTP 200 is the client's signal to close the socket itself.
        """
        await audio_buffer.stop_recording()
        # Allow a trailing STT commit (the last buffered audio) to arrive and
        # register as in-flight before we sample the drain state.
        await asyncio.sleep(settings.transcription_drain_secs)
        if await drain.wait_until_idle(settings.translation_drain_secs):
            logger.info("Session stop: translations finished")
        else:
            logger.warning(
                "Session stop: translations still running after {}s. Closing anyway...",
                settings.translation_drain_secs,
            )

    @transport.event_handler("on_client_connected")
    async def _on_client_connected(t: object, client: object) -> None:
        await audio_buffer.start_recording()

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(t: object, client: object) -> None:
        # The client closed the socket, either after /stop-session's drain, or
        # unexpectedly (network drop, tab closed).
        logger.info("Client disconnected: cancelling the pipeline")
        await audio_buffer.stop_recording()
        await worker.cancel()

    return stop_session


def build_pipeline(
    settings: Settings,
    websocket: WebSocket,
    *,
    stt_profile: str | None = None,
    target_language: str,
    target_languages: Sequence[str] | None = None,
    input_language: str,
    translator_profile: str | None = None,
    caption_publisher: CaptionPublisher | None = None,
    vad_silence_secs: float | None = None,
    vad_confidence: float | None = None,
    vad_threshold: float | None = None,
    max_segment_secs: float | None = None,
    noise_filter: NoiseFilter | None = None,
    use_glossary: bool = True,
) -> SessionPipeline:
    """Assemble the full Pipecat pipeline for one session on WebSocket.

    With a room (``caption_publisher``) or more than one target language, the
    translation stage is a FanOutTranslator that translates into every language
    and publishes captions to the room, otherwise it's a single LLMTranslator.

    Args:
        settings: The app settings.
        websocket: The speaker's dashboard connection.
        stt_profile: The STT profile id. (None/"" = first profile)
        target_language: The primary target language (the one the dashboard
            shows).
        target_languages: Every target language for fan-out, primary first;
            None if only one ``target_language`` is specified.
        input_language: The source language code, or "auto".
        translator_profile: The translator profile id. (None/"" = first
            profile)
        caption_publisher: Publishes captions to the session's room. (None
            if there is no room.)
        vad_silence_secs: Per-session endpoint silence: Silero's stop_secs for a
            batch profile, ElevenLabs' commit silence for streaming. (None =
            default)
        vad_confidence: Per-session Silero confidence. (None = default)
        vad_threshold: Per-session ElevenLabs VAD threshold.
        max_segment_secs: Per-session longest continuous speech before a batch
            profile's forced flush; 0 turns it off. (None = default)
        noise_filter: "off" or "rnnoise" (None = default)
        use_glossary: Whether to use the glossary. It has no effect when none
            is configured.

    Returns:
        The worker to run, the recorder, and the stop function.

    Raises:
        ValueError: ``stt_profile`` or ``translator_profile`` names no profile.
    """
    stt_config = settings.resolve_stt_profile(stt_profile)
    translator_config = settings.resolve_translator(translator_profile)
    targets = list(target_languages) if target_languages else [target_language]
    use_fanout = caption_publisher is not None or len(targets) > 1
    resolved_noise_filter = (
        noise_filter if noise_filter is not None else settings.noise_filter_default
    )
    # The glossary, when one is configured and the session hasn't turned it off.
    term_provider = (
        make_term_provider(settings, input_language) if use_glossary else None
    )
    drain = _TranslationDrain()

    recorder = _build_recorder(
        settings,
        stt_label=stt_config.label,
        input_language=input_language,
        targets=targets,
        translator_label=translator_config.label,
        use_fanout=use_fanout,
        noise_filter=resolved_noise_filter,
        glossary=term_provider is not None,
    )
    transport = _build_transport(settings, websocket, resolved_noise_filter)
    vad_processor, segment_flusher = _build_vad(
        settings,
        is_batch_stt=not stt_config.streaming,
        vad_silence_secs=vad_silence_secs,
        vad_confidence=vad_confidence,
        max_segment_secs=max_segment_secs,
    )
    stt = make_stt(
        stt_config,
        input_language,
        vad_silence_secs=vad_silence_secs,
        vad_threshold=vad_threshold,
    )
    translator = _build_translator(
        settings,
        translator_config,
        targets=targets,
        use_fanout=use_fanout,
        caption_publisher=caption_publisher,
        term_provider=term_provider,
        drain=drain,
        recorder=recorder,
    )
    audio_buffer = _build_audio_buffer(recorder)

    stages: list[FrameProcessor] = [transport.input(), audio_buffer, vad_processor]
    if segment_flusher is not None:
        stages.append(segment_flusher)
    stages += [stt, translator, transport.output()]

    worker = PipelineWorker(
        Pipeline(stages),
        params=PipelineParams(enable_metrics=True),
        observers=[_TranscriptObserver(recorder, stt, drain)],
    )
    stop = _add_session_handlers(settings, transport, audio_buffer, worker, drain)
    return SessionPipeline(worker=worker, recorder=recorder, stop=stop)
