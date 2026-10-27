"""Fan-out of translation across multiple languages for a single transcript.

``FanOutTranslator`` replaces ``LLMTranslator`` when a session has a room or
more than one target language. It makes one LLM call per language, all at once.
For each utterance it produces:

- **Dashboard frames,** for the first (primary) language only: the same LLM
  frames as ``LLMTranslator``, so the dashboard's translation column works as
  in a single-language session.
- **Captions,** for every language: partials and a final through the caption
  publisher, plus the untranslated transcript on the ``source`` language.
- **Recordings and metrics,** for every language: the final translation and
  glossary record go to the recorder, and a latency ``MetricsFrame`` named
  ``"<label> · <language>"`` gives the dashboard one card per language.

One language failing is reported but does not stop the others, and stopping
a session waits for every language.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    TranscriptionFrame,
)
from pipecat.metrics.metrics import MetricsData, ProcessingMetricsData, TTFBMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.delivery.rooms import SOURCE_LANG
from app.translation.prompts import (
    ChatClient,
    build_messages,
    build_system_prompt,
    build_system_prompt_with_terms,
)
from app.translation.terminology import (
    GlossaryTerm,
    TermProvider,
    build_term_record,
    format_term_block,
    lookup_terms,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class CaptionSink(Protocol):
    """The part of ``app.delivery.captions.CaptionPublisher`` that fan-out uses."""

    async def publish_partial(
        self, lang: str, utterance_id: str, text: str, sequence: int
    ) -> None:
        """Publish a partial caption (the translation so far).

        Args:
            lang: The caption language.
            utterance_id: The utterance it belongs to.
            text: The whole text so far.
            sequence: The message's number within the utterance.
        """
        ...

    async def publish_final(
        self, lang: str, utterance_id: str, text: str, sequence: int
    ) -> None:
        """Publish a final caption.

        Args:
            lang: The caption language.
            utterance_id: The utterance it belongs to.
            text: The final text.
            sequence: The message's number within the utterance.
        """
        ...


@dataclass
class _MetricAcc:
    """Running last/avg/min/max for one latency series (values in seconds).

    Attributes:
        count: How many values were added.
        last: The latest value.
        total: The sum of all values (for the average).
        minimum: The smallest value.
        maximum: The largest value.
    """

    count: int = 0
    last: float = 0.0
    total: float = 0.0
    minimum: float = float("inf")
    maximum: float = 0.0

    def add(self, value: float) -> None:
        """Add one measurement.

        Args:
            value: The latency, in seconds.
        """
        self.count += 1
        self.last = value
        self.total += value
        self.minimum = min(self.minimum, value)
        self.maximum = max(self.maximum, value)

    def summary_ms(self) -> dict[str, float]:
        """Summarise the series in milliseconds.

        Returns:
            The last, average, minimum and maximum, rounded to 0.1ms.
            All are zeros when nothing was recorded.
        """
        if self.count == 0:
            return {"last": 0.0, "avg": 0.0, "min": 0.0, "max": 0.0}
        return {
            "last": round(self.last * 1000, 1),
            "avg": round(self.total / self.count * 1000, 1),
            "min": round(self.minimum * 1000, 1),
            "max": round(self.maximum * 1000, 1),
        }


@dataclass
class _LangState:
    """One target language's prompt, rolling window and latency series.

    Attributes:
        language: The target-language code.
        system_prompt: The system prompt for this language.
        window: Recent (transcript, translation) pairs in this language; windows
            never mix languages.
        ttfb: Time to the first token.
        processing: Time to the end of the stream.
    """

    language: str
    system_prompt: str
    window: deque[tuple[str, str]]
    ttfb: _MetricAcc = field(default_factory=_MetricAcc)
    processing: _MetricAcc = field(default_factory=_MetricAcc)


class FanOutTranslator(FrameProcessor):
    """Translate each finalized transcript into every target language at once."""

    def __init__(
        self,
        *,
        client: ChatClient,
        model: str,
        target_languages: Sequence[str],
        system_prompt_template: str,
        rolling_window_n: int,
        publisher: CaptionSink | None = None,
        extra_body: dict[str, object] | None = None,
        on_unit_done: Callable[[], None] | None = None,
        on_translation: Callable[[str, str], None] | None = None,
        processor_label: str = "SEA-LION",
        term_provider: TermProvider | None = None,
        on_glossary: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        """Build the translator for a session's target languages.

        Args:
            client: The OpenAI-compatible chat client.
            model: The model name sent in every request.
            target_languages: Every target language. The first is the primary,
                which also drives the dashboard's LLM frames.
            system_prompt_template: The prompt from ``Settings.load_system_prompt``,
                filled in for each language.
            rolling_window_n: How many previous (transcript, translation) pairs
                travel with each prompt.
            publisher: Publishes captions to the room.
            extra_body: Per-model request-body params, sent only when non-empty.
            on_unit_done: Called once per transcript, after every language is done.
            on_translation: Receives (language, translation) for the recorder.
            processor_label: Base name of the per-language metric cards, e.g.
                "SEA-LION" for "SEA-LION · km".
            term_provider: The glossary's term provider.
            on_glossary: Receives (language, glossary record) for the recorder.

        Raises:
            ValueError: target_languages is empty.
        """
        super().__init__()
        if not target_languages:
            raise ValueError("target_languages must not be empty")
        self._client = client
        self._model = model
        self._extra_body = extra_body or {}
        self._publisher = publisher
        self._on_unit_done = on_unit_done
        self._on_translation = on_translation
        self._processor_label = processor_label
        self._term_provider = term_provider
        self._on_glossary = on_glossary
        self._langs = [
            _LangState(
                language=lang,
                system_prompt=build_system_prompt(system_prompt_template, lang),
                window=deque(maxlen=rolling_window_n),
            )
            for lang in target_languages
        ]
        self._utterances = 0

    def can_generate_metrics(self) -> bool:
        """Opt out of Pipecat's automatic metrics for this processor.

        Latency is reported per language instead, as hand-built ``MetricsFrame``s
        named after each language, so the automatic single-name metrics are off.

        Returns:
            False.
        """
        return False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Fan a finalized transcript out to every language; pass other frames on.

        Args:
            frame: The incoming frame.
            direction: The direction the frame is travelling in the pipeline.
        """
        await super().process_frame(frame, direction)

        # Same contract as LLMTranslator: act only on finalized transcripts
        # with content; forward everything else untouched. Serial process_frame
        # means a queued EndFrame drains only after this transcript's fan-out
        # completes for every language.
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self.push_frame(frame, direction)
            self._utterances += 1
            try:
                await self._fan_out(frame.text, f"u-{self._utterances}")
            finally:
                # Exactly once per transcript — even if some language failed —
                # so a drain wait can't hang and can't double-count.
                if self._on_unit_done is not None:
                    self._on_unit_done()
        else:
            await self.push_frame(frame, direction)

    async def _fan_out(self, text: str, utterance_id: str) -> None:
        """Publish the source caption, then translate into every language at once.

        Args:
            text: The finalized transcript.
            utterance_id: The per-session utterance id, "u-1", "u-2", …
        """
        if self._publisher is not None:
            await self._publisher.publish_final(
                SOURCE_LANG, utterance_id, text.strip(), sequence=0
            )
        await asyncio.gather(
            *(
                self._translate_language(state, text, utterance_id, primary=index == 0)
                for index, state in enumerate(self._langs)
            )
        )

    async def _translate_language(
        self, state: _LangState, text: str, utterance_id: str, *, primary: bool
    ) -> None:
        """Translate one utterance into one language, publishing and recording it.

        Args:
            state: The language's prompt, window and metrics.
            text: The finalized transcript.
            utterance_id: The per-session utterance id, "u-1", "u-2", etc.
            primary: Whether this language also drives the downstream LLM frames.
        """
        # Per-language latency window: call-start → first token (TTFB) and
        # call-start → stream end (processing). Measured for every language.
        started = time.monotonic()
        ttfb_secs: float | None = None
        terms = lookup_terms(self._term_provider, text, state.language)
        block = format_term_block(terms)
        create_kwargs: dict[str, object] = {
            "model": self._model,
            "messages": build_messages(
                build_system_prompt_with_terms(state.system_prompt, block),
                state.window,
                text,
            ),
            "stream": True,
        }
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body
        try:
            stream = await self._client.chat.completions.create(**create_kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            logger.error("Translation failed ({}): {!r}", state.language, e)
            await self.push_error(f"Translation failed ({state.language}).")
            self._record_glossary(
                state, text, utterance_id, terms, block, output=None, error=str(e)
            )
            return

        if primary:
            await self.push_frame(LLMFullResponseStartFrame())
        chunks: list[str] = []
        sequence = 0
        try:
            async for chunk in stream:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if not delta:
                    continue
                if ttfb_secs is None:
                    ttfb_secs = time.monotonic() - started
                chunks.append(delta)
                if primary:
                    await self.push_frame(LLMTextFrame(text=delta))
                if self._publisher is not None:
                    # Partials carry the FULL accumulated hypothesis — the
                    # client replace-renders, per the caption contract.
                    sequence += 1
                    await self._publisher.publish_partial(
                        state.language, utterance_id, "".join(chunks), sequence
                    )
        except Exception as e:
            if primary:
                await self.push_frame(LLMFullResponseEndFrame())
            logger.error("Translation failed mid-stream ({}): {!r}", state.language, e)
            await self.push_error(f"Translation failed ({state.language}).")
            self._record_glossary(
                state, text, utterance_id, terms, block, output=None, error=str(e)
            )
            return

        if primary:
            await self.push_frame(LLMFullResponseEndFrame())

        await self._emit_metrics(state, ttfb_secs, time.monotonic() - started)

        translation = "".join(chunks).strip()
        # An empty reply would teach the model that empty answers are fine.
        if translation:
            state.window.append((text, translation))
        if translation and self._on_translation is not None:
            self._on_translation(state.language, translation)
        self._record_glossary(
            state, text, utterance_id, terms, block, output=translation, error=None
        )
        if self._publisher is not None and translation:
            sequence += 1
            await self._publisher.publish_final(
                state.language, utterance_id, translation, sequence
            )

    def _record_glossary(
        self,
        state: _LangState,
        text: str,
        utterance_id: str,
        terms: list[GlossaryTerm],
        block: str | None,
        *,
        output: str | None,
        error: str | None,
    ) -> None:
        """Send one language's glossary record to the recorder, if the glossary is on.

        Args:
            state: The language the record is for.
            text: The finalized transcript.
            utterance_id: The per-session utterance id.
            terms: The utterance's terms for this language.
            block: The glossary block sent to the model, or None.
            output: The translation, or None when it failed.
            error: Why the translation failed, or None.
        """
        if self._term_provider is None or self._on_glossary is None:
            return
        self._on_glossary(
            state.language,
            build_term_record(
                utterance_id=utterance_id,
                source=text,
                source_language=self._term_provider.source_language,
                target_language=state.language,
                terms=terms,
                block=block,
                output=output,
                error=error,
            ),
        )

    async def _emit_metrics(
        self, state: _LangState, ttfb_secs: float | None, processing_secs: float
    ) -> None:
        """Record one language's latency and push it as a MetricsFrame.

        Args:
            state: The language.
            ttfb_secs: Time to the first token, or None if none arrived.
            processing_secs: Time to the end of the stream.
        """
        name = f"{self._processor_label} · {state.language}"
        data: list[MetricsData] = []
        if ttfb_secs is not None:
            state.ttfb.add(ttfb_secs)
            data.append(TTFBMetricsData(processor=name, value=ttfb_secs))
        state.processing.add(processing_secs)
        data.append(ProcessingMetricsData(processor=name, value=processing_secs))
        await self.push_frame(MetricsFrame(data=data))

    def metrics_summary(self) -> dict[str, dict[str, object]]:
        """Summarise each language's latency for the session's metadata.json.

        Returns:
            Per language: the number of translations, and the TTFB and processing
            summaries in milliseconds.
        """
        return {
            state.language: {
                "count": state.processing.count,
                "ttfb_ms": state.ttfb.summary_ms(),
                "processing_ms": state.processing.summary_ms(),
            }
            for state in self._langs
        }
