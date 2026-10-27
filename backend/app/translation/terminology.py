"""Glossary terms for each utterance, added to the translation prompt.

For each finalized transcript, the glossary finds the terms it mentions and how
each is written in the target language. The translator appends them to the prompt
and records them.

- ``TermProvider``: the interface the translators use, so they never import the
  glossary package.
- ``FileTermProvider``: the implementation over SEA-GULL's ``Glossary``.
- ``preload_glossary`` / ``make_term_provider``: load the glossary at startup, and
  build a session's term provider (None when the config file has no ``glossary``
  block).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from loguru import logger
from seagull import Glossary

from app.config import ConfigError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.config import Settings

# SEA-CAST and SEA-GULL both key languages by BCP-47 code ("ms"). "auto"
# leaves the spoken language to the STT provider, but a lookup needs a concrete one,
# so an auto-detected session, or one in a language the glossary has no forms for,
# is looked up as English.
DEFAULT_SOURCE_CODE = "en"

# sea-cast and glossary's prompt are decoupled as they serve different purposes
GLOSSARY_INSTRUCTION = (
    "Use these exact term translations wherever the source term appears:"
)

# Raise whenever GLOSSARY_INSTRUCTION or the block's line format changes, so the
# glossary records from different prompt wordings can be told apart.
PROMPT_VERSION = 1


@dataclass(frozen=True)
class GlossaryTerm:
    """One entity the utterance names, with its renderings in the target language."""

    concept_id: str
    source_form: str
    renderings: tuple[str, ...]  # best first; never empty

    @property
    def line(self) -> str:
        """Mapping of source to target forms, to be added to the prompt."""
        return f"{self.source_form} -> {self.renderings[0]}"


class TermProvider(Protocol):
    """Looks up an utterance's terms. Translators call it before each prompt."""

    source_language: str

    def terms(self, text: str, target_language: str) -> list[GlossaryTerm]:
        """Return the text's terms that have a rendering in the target language.

        Args:
            text: One finalized transcript.
            target_language: The target-language code, such as "km".

        Returns:
            Records in lookup order.
        """
        ...


def lookup_terms(
    source: TermProvider | None, text: str, target_language: str
) -> list[GlossaryTerm]:
    """Return the utterance's terms. No provider / lookup fails = [].

    A failed lookup is logged and the utterance is translated without terminology.

    Args:
        source: The session's term provider, or None when the glossary is off.
        text: One finalized transcript.
        target_language: The target-language code.

    Returns:
        The source's terms, or [] when source is None or its lookup raised.
    """
    if source is None:
        return []
    try:
        return source.terms(text, target_language)
    except Exception:
        logger.exception("Glossary lookup failed; translating without terminology")
        return []


def format_term_block(terms: Sequence[GlossaryTerm]) -> str | None:
    """Format the utterance's terms as the block appended to the system prompt.

    The block is GLOSSARY_INSTRUCTION followed by one "source form -> target form" line
    per term.

    Args:
        terms: The utterance's terms, in lookup order.

    Returns:
        The instruction sentence followed by one line per term, or None when there
        is nothing to add, so the caller leaves the prompt exactly as it was.
    """
    if not terms:
        return None
    return GLOSSARY_INSTRUCTION + "\n" + "\n".join(t.line for t in terms)


def build_term_record(
    *,
    utterance_id: str,
    source: str,
    source_language: str,
    target_language: str,
    terms: Sequence[GlossaryTerm],
    block: str | None,
    output: str | None,
    error: str | None = None,
) -> dict[str, object]:
    """Build the per-utterance glossary record the recorder writes.

    A record is written for a failed translation too (``output`` None, ``error`` set).

    Args:
        utterance_id: The translator's per-session counter, "u-1", "u-2", …
        source: The transcript text.
        source_language: The code the glossary looked the speech up under.
        target_language: The target-language code this record is for.
        terms: The utterance's terms, in lookup order.
        block: The formatted block sent to the model, or None.
        output: The model's translation, stripped; None when it failed.
        error: Why the translation failed, or None.

    Returns:
        A JSON-serialisable dict: the prompt version, language codes, the terms as
        dicts, the block, and the output or error.
    """
    return {
        "prompt_version": PROMPT_VERSION,
        "utterance_id": utterance_id,
        "source_lang": source_language,
        "target_lang": target_language,
        "source": source,
        "terms": [
            {
                "concept_id": t.concept_id,
                "source_form": t.source_form,
                "renderings": list(t.renderings),
            }
            for t in terms
        ],
        "block": block,
        "output": output,
        "error": error,
    }


# ── File term provider: SEA-GULL's library, in-process ───────────────────────


@lru_cache
def load_glossary(jsonl_path: str) -> Glossary:
    """Load and validate the glossary once per process, compiling every matcher.

    Args:
        jsonl_path: The SEA-GULL glossary JSONL file.

    Returns:
        The process-wide Glossary for that path.

    Raises:
        GlossaryError: If an entry breaks the glossary format.
    """
    glossary = Glossary.load(jsonl_path)
    glossary.precompile_matchers()
    return glossary


class FileTermProvider:
    """``TermProvider`` over SEA-GULL for one session's spoken language."""

    def __init__(self, glossary: Glossary, source_language: str) -> None:
        """Bind the glossary to a session's spoken language.

        Args:
            glossary: The process-wide Glossary.
            source_language: The session's spoken-language code. "auto" and codes
                the glossary has no forms for are looked up as English.
        """
        self._glossary = glossary
        self._languages = frozenset(glossary.languages())
        self.source_language = (
            source_language
            if source_language in self._languages
            else DEFAULT_SOURCE_CODE
        )

    def terms(self, text: str, target_language: str) -> list[GlossaryTerm]:
        """Look the text up and turn each concept found into a GlossaryTerm.

        Args:
            text: One finalized transcript.
            target_language: The target-language code.

        Returns:
            One GlossaryTerm per concept, in order of first appearance: its first
            mention as written in the text and its target forms in glossary order.
            [] when the glossary has no forms for either language, or the target
            equals the spoken language.
        """
        if (
            self.source_language not in self._languages
            or target_language not in self._languages
            or target_language == self.source_language
        ):
            return []
        return [
            GlossaryTerm(
                result.concept_id,
                result.matched[0].text,
                tuple(target.form for target in result.targets),
            )
            for result in self._glossary.lookup(
                text, self.source_language, target_language
            )
        ]


def preload_glossary(settings: Settings) -> None:
    """Load the configured glossary at startup, and log what it holds.

    Loading caches the glossary for the process, so sessions start without waiting
    for it, and a problem stops the server at startup instead of the first speaker's
    session.

    Args:
        settings: The application settings; only ``glossary`` is read.

    Raises:
        ConfigError: The file isn't a valid glossary.
    """
    if settings.glossary is None:
        return
    path = settings.glossary.path
    started = time.monotonic()
    try:
        glossary = load_glossary(str(path))
    except ValueError as e:  # SEA-GULL's GlossaryError is a ValueError
        raise ConfigError(f"glossary.path {path} is not a valid glossary: {e}") from e
    logger.info(
        "Glossary: {} ({} concepts; languages {}; loaded in {:.1f}s)",
        path,
        len(glossary),
        ", ".join(glossary.languages()),
        time.monotonic() - started,
    )


def make_term_provider(settings: Settings, input_language: str) -> TermProvider | None:
    """Build the session's term provider, or None when the glossary is not configured.

    Args:
        settings: The application settings; only ``glossary`` is read.
        input_language: The session's spoken-language code, or "auto".

    Returns:
        A FileTermProvider over the process-wide glossary, or None when the config file
        has no glossary block.
    """
    if settings.glossary is None:
        return None
    return FileTermProvider(load_glossary(str(settings.glossary.path)), input_language)
