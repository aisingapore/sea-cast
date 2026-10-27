"""Single-language translation of final transcripts with an LLM.

Used for a session with one target language and no audience room; anything
else goes through the fan-out translator (app.translation.fanout). The
translation is streamed to the speaker dashboard as LLMTextFrames, with the last
N (transcript, translation) pairs sent as context.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

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
    from collections.abc import Callable

    from app.config import Settings


class LLMTranslator(FrameProcessor):
    """Translates each final transcript into one language with an LLM.

    The transcript is passed on unchanged, followed by the translation streamed
    as ``LLMTextFrame``s between ``LLMFullResponseStartFrame`` and
    ``LLMFullResponseEndFrame``.
    """

    def __init__(
        self,
        *,
        client: ChatClient,
        model: str,
        target_language: str,
        system_prompt_template: str,
        rolling_window_n: int,
        extra_body: dict[str, object] | None = None,
        on_unit_done: Callable[[], None] | None = None,
        on_translation: Callable[[str, str], None] | None = None,
        term_provider: TermProvider | None = None,
        on_glossary: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        """Create the translator for one language.

        Args:
            client: An AsyncOpenAI-compatible client.
            model: The model name sent in every request.
            target_language: The language code to translate into.
            system_prompt_template: The prompt from ``Settings.load_system_prompt``.
            rolling_window_n: How many previous (source, translation) pairs are
                sent with each request.
            extra_body: Extra request-body fields for this model, sent only when
                non-empty.
            on_unit_done: Called once per final transcript when its translation
                finishes or fails.
            on_translation: Called with (target language, translation) for each
                non-empty translation, for the recorder.
            term_provider: Looks up glossary terms for each utterance; None sends
                no terms.
            on_glossary: Called with (target language, record) after each
                translation, when there is a term provider.
        """
        super().__init__()
        self._client = client
        self._model = model
        self._target_language = target_language
        self._system_prompt = build_system_prompt(
            system_prompt_template, target_language
        )
        self._extra_body = extra_body or {}
        self._window: deque[tuple[str, str]] = deque(maxlen=rolling_window_n)
        self._on_unit_done = on_unit_done
        self._on_translation = on_translation
        self._term_provider = term_provider
        self._on_glossary = on_glossary
        self._utterances = 0

    def can_generate_metrics(self) -> bool:
        """Turn on Pipecat metrics, so translation latency reaches RTVI.

        Returns:
            True.
        """
        return True

    def _build_messages(
        self, text: str, block: str | None = None
    ) -> list[dict[str, str]]:
        """Compose the request messages for one utterance.

        Args:
            text: The finalized transcript.
            block: The utterance's glossary block, or None.

        Returns:
            The system message (with the block appended), the rolling window,
            then the utterance.
        """
        return build_messages(
            build_system_prompt_with_terms(self._system_prompt, block),
            self._window,
            text,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Pass every frame on, translating final transcripts after passing them.

        Args:
            frame: The incoming frame.
            direction: The direction it travels in.
        """
        await super().process_frame(frame, direction)

        # Only act on finalized transcripts with content; everything else (incl.
        # interim transcripts, lifecycle frames) flows through untouched. Because
        # process_frame runs serially, a queued EndFrame (from graceful shutdown)
        # is forwarded only after the preceding transcript's _translate() returns,
        # so every queued utterance is translated before the pipeline ends.
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self.push_frame(frame, direction)  # keep transcript flowing
            try:
                await self._translate(frame.text)
            finally:
                # Signal completion even on error so a drain wait can't hang.
                if self._on_unit_done is not None:
                    self._on_unit_done()
        else:
            await self.push_frame(frame, direction)

    async def _translate(self, text: str) -> None:
        """Translate one finalized transcript, streaming deltas downstream.

        Args:
            text: The finalized transcript.
        """
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()

        self._utterances += 1
        terms = lookup_terms(self._term_provider, text, self._target_language)
        block = format_term_block(terms)
        create_kwargs: dict[str, object] = {
            "model": self._model,
            "messages": self._build_messages(text, block),
            "stream": True,
        }
        if self._extra_body:
            create_kwargs["extra_body"] = self._extra_body
        try:
            stream = await self._client.chat.completions.create(**create_kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
            logger.error("Translation failed: {!r}", e)
            await self.push_error("Translation failed.")
            self._record_glossary(text, terms, block, output=None, error=str(e))
            return

        await self.push_frame(LLMFullResponseStartFrame())
        chunks: list[str] = []
        ttfb_stopped = False
        try:
            async for chunk in stream:
                delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                if delta:
                    if not ttfb_stopped:
                        await self.stop_ttfb_metrics()
                        ttfb_stopped = True
                    chunks.append(delta)
                    await self.push_frame(LLMTextFrame(text=delta))
        except Exception as e:
            if not ttfb_stopped:
                await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())
            logger.error("Translation failed mid-stream: {!r}", e)
            await self.push_error("Translation failed.")
            self._record_glossary(text, terms, block, output=None, error=str(e))
            return

        if not ttfb_stopped:
            await self.stop_ttfb_metrics()
        await self.stop_processing_metrics()
        await self.push_frame(LLMFullResponseEndFrame())

        translation = "".join(chunks).strip()
        # An empty reply would teach the model that empty answers are fine.
        if translation:
            self._window.append((text, translation))
            if self._on_translation is not None:
                self._on_translation(self._target_language, translation)
        self._record_glossary(text, terms, block, output=translation, error=None)

    def _record_glossary(
        self,
        text: str,
        terms: list[GlossaryTerm],
        block: str | None,
        *,
        output: str | None,
        error: str | None,
    ) -> None:
        """Send the utterance's glossary record to the recorder, if there is one.

        Args:
            text: The finalized transcript.
            terms: The utterance's terms.
            block: The glossary block sent to the model, or None.
            output: The translation, or None when it failed.
            error: Why the translation failed, or None.
        """
        if self._term_provider is None or self._on_glossary is None:
            return
        self._on_glossary(
            self._target_language,
            build_term_record(
                utterance_id=f"u-{self._utterances}",
                source=text,
                source_language=self._term_provider.source_language,
                target_language=self._target_language,
                terms=terms,
                block=block,
                output=output,
                error=error,
            ),
        )


def make_translator(
    settings: Settings,
    client: ChatClient,
    target_language: str,
    model: str,
    extra_body: dict[str, object] | None = None,
    on_unit_done: Callable[[], None] | None = None,
    on_translation: Callable[[str, str], None] | None = None,
    term_provider: TermProvider | None = None,
    on_glossary: Callable[[str, dict[str, object]], None] | None = None,
) -> LLMTranslator:
    """Create a translator, reading the system prompt and window size from settings.

    Args:
        settings: The app settings.
        client: An AsyncOpenAI-compatible client.
        target_language: The language code to translate into.
        model: The model name sent in every request.
        extra_body: Extra request-body fields for this model.
        on_unit_done: Called once per final transcript when its translation
            finishes or fails.
        on_translation: Called with (target language, translation) for each
            non-empty translation, or None.
        term_provider: Looks up glossary terms for each utterance, or None.
        on_glossary: Called with each utterance's glossary record, or None.

    Returns:
        The configured LLMTranslator.
    """
    return LLMTranslator(
        client=client,
        model=model,
        target_language=target_language,
        system_prompt_template=settings.load_system_prompt(),
        rolling_window_n=settings.rolling_window_n,
        extra_body=extra_body,
        on_unit_done=on_unit_done,
        on_translation=on_translation,
        term_provider=term_provider,
        on_glossary=on_glossary,
    )
