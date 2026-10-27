"""Tell ``quickstart.sh`` which models to run for whisper.cpp and Ollama.

Reads ``sea-cast.yaml`` (as the server does, so a broken config fails here first):

- whisper.cpp: the model of the ``openai`` STT profile at ``localhost:<port>``.
- Ollama: the models of the translator profiles at ``localhost:11434``.

quickstart.sh runs it from ``backend/`` and ``eval``s the output:

    $ uv run python -m scripts.local_services --whisper-port 8000
    WHISPER_PROFILE_MODEL=large-v3-turbo-q5_0
    OLLAMA_MODELS=aisingapore/Gemma-SEA-LION-v4.5-E2B-IT

A value is empty when no profile uses that service.
"""

import argparse
import shlex
import sys
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.config import (
    _LOCAL_HOSTS,
    ConfigError,
    OpenAISttProfile,
    Settings,
    get_settings,
)

OLLAMA_PORT = 11434


@dataclass(frozen=True)
class LocalServices:
    """The local services the config uses, and their models.

    Attributes:
        whisper_model: The whisper.cpp model name, or None if no profile uses it.
        ollama_models: The Ollama models, in profile order, without duplicates.
    """

    whisper_model: str | None
    ollama_models: list[str]


def _is_local(base_url: str, port: int) -> bool:
    """Whether ``base_url`` points at ``port`` on this machine.

    Args:
        base_url: A profile's base URL.
        port: The port to match.

    Returns:
        True for a loopback host on that port.
    """
    url = urlsplit(base_url)
    return url.hostname in _LOCAL_HOSTS and url.port == port


def find_local_services(settings: Settings, whisper_port: int) -> LocalServices:
    """Find the profiles that use the script's whisper.cpp and Ollama.

    Args:
        settings: The loaded settings.
        whisper_port: The port the script runs whisper.cpp on.

    Returns:
        The whisper.cpp model (the first matching profile's) and the Ollama models.

    Raises:
        ConfigError: Two whisper.cpp profiles name different models; one server
            can only load one.
    """
    whisper = [
        p
        for p in settings.stt_profiles
        if isinstance(p, OpenAISttProfile) and _is_local(p.base_url, whisper_port)
    ]
    if len({p.model for p in whisper}) > 1:
        raise ConfigError(
            f"stt_profiles {', '.join(repr(p.id) for p in whisper)} all point at "
            f"whisper.cpp on localhost:{whisper_port} but name different models. "
            "That port can only run one model. To use a second model, run another "
            "whisper-server yourself on another port and point one profile's "
            "base_url at it (quickstart.sh won't start it)."
        )
    ollama = [
        p.model
        for p in settings.translator_profiles
        if _is_local(p.base_url, OLLAMA_PORT)
    ]
    return LocalServices(
        whisper_model=whisper[0].model if whisper else None,
        ollama_models=list(dict.fromkeys(ollama)),
    )


def main() -> None:
    """Print the local services as shell assignments, or the config error."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--whisper-port", type=int, required=True)
    args = parser.parse_args()
    try:
        services = find_local_services(get_settings(), args.whisper_port)
    except ConfigError as e:
        sys.exit(f"error: {e}")
    print(f"WHISPER_PROFILE_MODEL={shlex.quote(services.whisper_model or '')}")
    print(f"OLLAMA_MODELS={shlex.quote(' '.join(services.ollama_models))}")


if __name__ == "__main__":
    main()
