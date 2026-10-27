"""Language checks the speaker and room routes share."""

from typing import get_args

from app.languages import InputLanguage, TargetLanguage

VALID_TARGET_LANGUAGES: frozenset[str] = frozenset(get_args(TargetLanguage))
VALID_INPUT_LANGUAGES: frozenset[str] = frozenset(get_args(InputLanguage))


def drop_source_language(targets: list[str], input_language: str) -> list[str]:
    """Remove the source language from the targets, unless it's the only one.

    Translating a language into itself wastes a translation call per utterance;
    the audience can pick the source channel instead. ``POST /rooms`` and
    ``/ws`` both apply this, so a room's languages match its session's exactly
    (the speaker connect is rejected otherwise).

    Args:
        targets: The target language codes, primary first.
        input_language: The source language code, or "auto".

    Returns:
        The targets without the source language, or just the first target when
        that would leave none.
    """
    others = [lang for lang in targets if lang != input_language]
    return others if others else targets[:1]
