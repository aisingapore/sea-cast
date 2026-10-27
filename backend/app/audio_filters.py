"""Background noise filters for the input audio.

A session's ``noise_filter`` picks one. It's applied at the beginning of the transport
input, so both VAD and STT hear the filtered audio. Each filter is a Pipecat
``BaseAudioFilter``. To add one, add a value to ``NoiseFilter`` and a branch to
``make_audio_in_filter``, and the option to the dashboard's noise filter dropdown
(``App.tsx``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, assert_never, cast, get_args

from loguru import logger

if TYPE_CHECKING:
    from pipecat.audio.filters.base_audio_filter import BaseAudioFilter

NoiseFilter = Literal["off", "rnnoise"]
VALID_NOISE_FILTERS: frozenset[str] = frozenset(get_args(NoiseFilter))


def parse_noise_filter(raw: str) -> NoiseFilter:
    """Check a noise-filter name from outside the app (e.g. a query param).

    Args:
        raw: The name.

    Returns:
        The name, as a ``NoiseFilter``.

    Raises:
        ValueError: ``raw`` isn't one of ``VALID_NOISE_FILTERS``.
    """
    if raw in VALID_NOISE_FILTERS:
        return cast("NoiseFilter", raw)
    raise ValueError(
        f"noise_filter must be one of {sorted(VALID_NOISE_FILTERS)}, got {raw!r}"
    )


def make_audio_in_filter(name: NoiseFilter) -> BaseAudioFilter | None:
    """Return the transport's input filter for a noise filter.

    Args:
        name: The session's noise filter.

    Returns:
        The filter, or None for "off".
    """
    if name == "off":
        return None
    if name == "rnnoise":
        # Imported here so the app only loads RNNoise when a session uses it.
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter

        logger.info("Audio input filter enabled: rnnoise")
        # RNNoise works at 48 kHz, so it resamples the 16 kHz input. The default
        # "QQ" resampler quality has the lowest latency.
        return RNNoiseFilter()
    assert_never(name)
