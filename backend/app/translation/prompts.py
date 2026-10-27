"""Chat client and prompt building utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import langcodes

from app.config import LANGUAGE_PLACEHOLDER
from app.languages import DISPLAY_OVERRIDES

if TYPE_CHECKING:
    from collections.abc import Iterable


class ChatClient(Protocol):
    """The part of an AsyncOpenAI-compatible client the translator uses."""

    @property
    def chat(self) -> object:
        """The chat API. The translator calls ``chat.completions.create``."""
        ...


def get_language_display_name(code: str) -> str:
    """Return the English name of a language, for prompts.

    Args:
        code: A language code from languages.yaml.

    Returns:
        The name from languages.yaml's display overrides if it has one, else the
        name langcodes gives.
    """
    override = DISPLAY_OVERRIDES.get(code)
    if override is not None:
        return override
    return str(langcodes.Language.get(code).display_name())


def build_system_prompt(system_prompt_template: str, target_language: str) -> str:
    """Return the system prompt for translating into one language.

    Both translators use it, so they build the same prompt for a language.

    Args:
        system_prompt_template: The prompt from ``Settings.load_system_prompt``.
        target_language: The language code to translate into.

    Returns:
        The prompt with every ``{language}`` replaced by the language's name.
    """
    return system_prompt_template.replace(
        LANGUAGE_PLACEHOLDER, get_language_display_name(target_language)
    )


def build_messages(
    system_prompt: str, window: Iterable[tuple[str, str]], text: str
) -> list[dict[str, str]]:
    """Return the chat messages for translating one utterance.

    Args:
        system_prompt: The system prompt.
        window: The previous (source, translation) pairs, oldest first.
        text: The utterance to translate.

    Returns:
        The system message, then each pair as a user and an assistant message,
        then the utterance as the last user message.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for source, translation in window:
        messages.append({"role": "user", "content": source})
        messages.append({"role": "assistant", "content": translation})
    messages.append({"role": "user", "content": text})
    return messages


def build_system_prompt_with_terms(system_prompt: str, block: str | None) -> str:
    """Return the system prompt with the utterance's term block appended.

    Args:
        system_prompt: The session's system prompt from build_system_prompt.
        block: The formatted block from format_term_block, or None.

    Returns:
        The prompt unchanged when block is None, else the prompt, a blank line
        and the block.
    """
    return system_prompt if block is None else f"{system_prompt}\n\n{block}"
