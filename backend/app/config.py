"""Manages the app's configuration.

Config is drawn from two places:

- ``sea-cast.yaml`` (path from ``SEA_CAST_CONFIG``), which defines the STT and
  translator profiles, the system prompt path and the glossary. See
  ``sea-cast.example.yaml``.
- The environment and a local ``.env`` file which defines secrets (named by the
  profiles), server settings and tuning dials. See ``.env.example``.

The settings are validated and errors are thrown at startup if present.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal, Self
from urllib.parse import urlsplit

import langcodes
import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.audio_filters import NoiseFilter
from app.languages import (
    APP_SOURCE,
    APP_TARGET,
    STT_MODELS,
    TRANSLATOR_MODELS,
    STTSettings,
)

# Relative to the working directory (backend/ when run as in the README).
DEFAULT_CONFIG_PATH = Path("sea-cast.yaml")

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Where the system prompt names the target language; filled in per language.
LANGUAGE_PLACEHOLDER = "{language}"


def _resolve_inline_languages(
    codes: list[str], app_languages: tuple[str, ...], field: str
) -> frozenset[str]:
    """Return the app languages in an inline list, as a preset's are resolved.

    An inline list describes the model's supported languages which may include
    languages the app doesn't offer. Those languages are dropped. The languages
    specified are also validated.

    Args:
        codes: The language codes the profile lists.
        app_languages: The app's source or target languages.
        field: The list's name (for writing error messages).

    Returns:
        The inline language codes that are app languages.

    Raises:
        ValueError: A code isn't a valid language code (likely a typo), or none of
            the codes is an app language.
    """
    invalid = [code for code in codes if not langcodes.tag_is_valid(code)]
    if invalid:
        raise ValueError(f"languages.{field} has invalid language codes: {invalid}")

    supported = frozenset(codes) & frozenset(app_languages)
    if not supported:
        raise ValueError(
            f"languages.{field} lists none of the app's languages {list(app_languages)}"
        )

    return supported


# 1. Translator Profiles


class TranslatorLanguages(BaseModel):
    """Target languages listed inline in a profile, instead of naming a preset.

    Attributes:
        translates_to: The languages the model can translate into.
    """

    model_config = ConfigDict(extra="forbid")

    translates_to: list[str] = Field(min_length=1)


def _resolve_translator_languages(
    languages: str | TranslatorLanguages | None,
) -> frozenset[str]:
    """Get the app target languages a translator profile covers.

    Args:
        languages: Represents the languages the model can translate into.
            May be a preset name, or TranslatorLanguages object, or None,
            which is taken to mean all app target languages are supported.

    Returns:
        The app target languages the profile can translate into.

    Raises:
        ValueError: An unknown preset name, an invalid code, or no app language.
    """
    # If languages is not provided, the profile supports all app languages.
    if languages is None:
        return frozenset(APP_TARGET)

    # If languages is a str, that means it is a preset.
    if isinstance(languages, str):
        preset = TRANSLATOR_MODELS.get(languages)
        if preset is None:
            raise ValueError(
                f"unknown translator_models preset {languages!r} in languages.yaml "
                f"(known: {sorted(TRANSLATOR_MODELS)})"
            )
        return preset["translates_to"]

    # Otherwise, resolve the profile's supported languages against the app's.
    return _resolve_inline_languages(
        languages.translates_to, APP_TARGET, "translates_to"
    )


class TranslatorProfile(BaseModel):
    """One selectable OpenAI-compatible translation endpoint/model.

    Listed under ``translator_profiles`` in ``sea-cast.yaml``. API keys are never
    defined in the file. ``api_key_env`` only names the env var holding the key,
    and startup fails if that var isn't set. Omit it for keyless servers (Ollama,
    llama.cpp, vLLM etc.), which get the placeholder key ``"none"``.

    Attributes:
        id: Unique id, sent by the dashboard as the session's ``translator``.
        label: Name shown in the dashboard's translator dropdown.
        base_url: The API root of an OpenAI-compatible chat endpoint, e.g.
            ``http://localhost:11434/v1``.
        model: The model name the server expects (e.g. as ``ollama list``
            shows it, or a LiteLLM route name).
        api_key_env: The env var holding the API key, or None for keyless
            servers.
        extra_body: Extra request-body params merged into every chat request
            (OpenAI SDK's ``extra_body``), for model-specific parameters.
        languages: A ``translator_models`` preset name, an inline
            TranslatorLanguages object, or None to specify all app target
            languages. The resolved set is ``translates_to``.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    base_url: str
    model: str
    api_key_env: str | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)
    languages: str | TranslatorLanguages | None = None

    _translates_to: frozenset[str] = PrivateAttr()

    @model_validator(mode="after")
    def _resolve_languages(self) -> Self:
        """Resolve ``languages`` once, when the profile is loaded."""
        self._translates_to = _resolve_translator_languages(self.languages)
        return self

    @property
    def translates_to(self) -> frozenset[str]:
        """The app target languages this profile can translate into."""
        return self._translates_to


# 2. STT Profiles


class BearerAuth(BaseModel):
    """``Authorization: Bearer <key>``, with the key read from ``api_key_env``.

    Attributes:
        type: Always ``bearer``.
        api_key_env: The env var holding the API key. Startup fails if it isn't
            set.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["bearer"]
    api_key_env: str


class BasicAuth(BaseModel):
    """HTTP Basic auth, with both parts read from the named env vars.

    Basic auth is only base64-encoded, not encrypted, so use an https:// base_url.

    Attributes:
        type: Always ``basic``.
        username_env: The env var holding the username. Startup fails if it
            isn't set.
        password_env: The env var holding the password. Startup fails if it
            isn't set.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["basic"]
    username_env: str
    password_env: str


class SttLanguages(BaseModel):
    """Languages listed inline in an STT profile, instead of naming a preset.

    Attributes:
        recognizes: The languages the model can recognise. Codes the app doesn't
            offer are dropped and an invalid code fails startup.
        supports_auto: Whether the model detects the spoken language automatically.
        auto_only: Whether auto-detect is the ONLY option (the model ignores any
            code it is sent). Needs supports_auto, or else nothing is selectable.
        code_overrides: {app code: code the model expects}, e.g. ``{"fil": "tl"}``
            for Whisper.
    """

    model_config = ConfigDict(extra="forbid")

    recognizes: list[str] = Field(min_length=1)
    supports_auto: bool = False
    auto_only: bool = False
    code_overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _auto_only_needs_auto(self) -> Self:
        """Reject auto_only without supports_auto.

        Nothing is selectable if supports_auto is also False.
        """
        if self.auto_only and not self.supports_auto:
            raise ValueError("auto_only: true needs supports_auto: true")
        return self


def _resolve_stt_languages(languages: str | SttLanguages | None) -> STTSettings:
    """Get the app source languages an STT profile recognises.

    This also includes whether the model supports auto-detection of languages
    and whether it ONLY supports auto-detection.

    Args:
        languages: Represents the languages the model can recognise.
            May be a preset name, or SttLanguages object, or None, which
            is taken to mean all app source languages are supported.

    Returns:
        The app source languages the profile can recognise, with its
        auto-detect flags and code overrides.

    Raises:
        ValueError: An unknown preset name, an invalid code, or no app language.
    """
    # If languages is not provided, the profile supports all app languages,
    # without auto-detect.
    if languages is None:
        return {
            "recognizes": frozenset(APP_SOURCE),
            "supports_auto": False,
            "auto_only": False,
            "code_overrides": {},
        }

    # If languages is a str, that means it is a preset.
    if isinstance(languages, str):
        preset = STT_MODELS.get(languages)
        if preset is None:
            raise ValueError(
                f"unknown stt_models preset {languages!r} in languages.yaml "
                f"(known: {sorted(STT_MODELS)})"
            )
        return preset

    # Otherwise, resolve the profile's supported languages against the app's.
    return {
        "recognizes": _resolve_inline_languages(
            languages.recognizes, APP_SOURCE, "recognizes"
        ),
        "supports_auto": languages.supports_auto,
        "auto_only": languages.auto_only,
        "code_overrides": dict(languages.code_overrides),
    }


class _SttProfileBase(BaseModel):
    """Fields every STT profile has regardless of its adapter type.

    Attributes:
        id: Unique id, sent by the dashboard as the session's ``stt_profile``.
        label: Name shown in the dashboard's transcriber dropdown.
        model: The model name the server expects.
        languages: An ``stt_models`` preset name, an inline SttLanguages object,
            or None for every app source language without auto-detect (with a
            startup warning). The resolved result is ``resolved_languages``.
        streaming: Set by each adapter class, not the YAML. True for streaming
            classes, and False for batch-based classes.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    model: str
    languages: str | SttLanguages | None = None
    streaming: ClassVar[bool]

    _resolved_languages: STTSettings = PrivateAttr()

    @model_validator(mode="after")
    def _resolve_languages(self) -> Self:
        """Resolve ``languages`` once, when the profile is loaded."""
        self._resolved_languages = _resolve_stt_languages(self.languages)
        return self

    @property
    def resolved_languages(self) -> STTSettings:
        """The app source languages it recognises, auto-detect flags and overrides."""
        return self._resolved_languages


class OpenAISttOptions(BaseModel):
    """Tuning for the ``openai`` adapter.

    Attributes:
        timeout_secs: Per-request timeout, 15s by default. A request still running
            after this is dropped, and that utterance gets no transcript
            (requests are never retried).
    """

    model_config = ConfigDict(extra="forbid")

    timeout_secs: float = Field(default=15.0, gt=0)


class OpenAISttProfile(_SttProfileBase):
    """Any OpenAI-compatible ``POST /audio/transcriptions`` endpoint (batch).

    Attributes:
        adapter: Always ``openai``.
        base_url: The API root, e.g. ``http://localhost:8000/v1``; the service
            appends ``/audio/transcriptions``.
        auth: BearerAuth or BasicAuth, or None for keyless servers (which get a
            placeholder Bearer key they ignore).
        options: Adapter tuning (see OpenAISttOptions).
    """

    streaming: ClassVar[bool] = False

    adapter: Literal["openai"]
    base_url: str
    auth: Annotated[BearerAuth | BasicAuth, Field(discriminator="type")] | None = None
    options: OpenAISttOptions = Field(default_factory=OpenAISttOptions)


class ElevenLabsSttOptions(BaseModel):
    """Tuning for the ``elevenlabs`` adapter. The dashboard can override per session.

    Attributes:
        vad_silence_secs: Seconds of trailing silence (API allows 0.3-3.0, default 0.6)
            before ElevenLabs' server-side VAD commits a transcript, which delays the
            caption by just as much. If lowered, the STT service commits more eagerly,
            but long mid-sentence pauses may then split an utterance.
        vad_threshold: VAD sensitivity (API allows 0.1-0.9, lower is more sensitive).
            0.4 is ElevenLabs' and our default.
    """

    model_config = ConfigDict(extra="forbid")

    vad_silence_secs: float = Field(default=0.6, ge=0.3, le=3.0)
    vad_threshold: float = Field(default=0.4, ge=0.1, le=0.9)


class ElevenLabsSttProfile(_SttProfileBase):
    """ElevenLabs realtime STT over a WebSocket (streaming, server-side VAD).

    Attributes:
        adapter: Always ``elevenlabs``.
        base_url: The API's hostname only (the service prepends ``wss://``);
            ``api.elevenlabs.io`` by default. Override for a proxy.
        auth: Required, and always BearerAuth. Only its ``api_key_env`` is used.
            Pipecat sends the key in ElevenLabs' own ``xi-api-key`` header, not
            as a Bearer token. (But ``type: bearer`` is maintained for consistency
            with other adapters).
        options: Adapter tuning (see ElevenLabsSttOptions).
    """

    streaming: ClassVar[bool] = True

    adapter: Literal["elevenlabs"]
    base_url: str = "api.elevenlabs.io"
    auth: BearerAuth
    options: ElevenLabsSttOptions = Field(default_factory=ElevenLabsSttOptions)


SttProfile = Annotated[
    OpenAISttProfile | ElevenLabsSttProfile, Field(discriminator="adapter")
]

# 3. Glossary


class GlossaryConfig(BaseModel):
    """The glossary config. Omit it to turn the glossary off.

    Attributes:
        path: A SEA-GULL glossary JSONL file.
    """

    model_config = ConfigDict(extra="forbid")

    path: Path


# 4. Settings


class Settings(BaseSettings):
    """Contains all of the app's settings (typed and validated).

    The profile lists, the system prompt path and the glossary come from
    ``sea-cast.yaml`` (see ConfigFile; loaded by load_settings). Every other field
    comes from the environment / ``.env``, named in upper case (``vad_stop_secs`` ←
    ``VAD_STOP_SECS``). Refer to comments above each attribute for more info.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # A blank value (``CORS_ORIGINS=`` in .env, or an empty env var) means
        # "use the default", instead of failing to parse as a list, number or path.
        env_ignore_empty=True,
    )

    # ── STT ────────────────────────────────────────────────────────────────
    # Set in sea-cast.yaml (see ConfigFile).
    # At least one entry is required, with the first as the default.
    stt_profiles: list[SttProfile] = Field(default_factory=list)

    # ── Noise filter ────────────────────────────────────────────────────────
    # Background-noise filter applied at the transport input, before VAD/STT.
    # "rnnoise" enables Pipecat's local RNNoiseFilter for noisy venues.
    noise_filter_default: NoiseFilter = "off"

    # ── Translation (OpenAI-compatible endpoints) ───────────────────────────
    # Set in sea-cast.yaml (see ConfigFile).
    # At least one entry is required, with the first as the default.
    translator_profiles: list[TranslatorProfile] = Field(default_factory=list)

    # ── Output path ──────────────────────────────────────────────────────────
    recordings_dir: Path = Path("./recordings")

    # ── VAD tuning dials ─────────────────────────────────────────────────────
    # Seconds of continuous speech before Silero reports "speech started".
    # Higher = ignores more false starts, but may miss very short words.
    vad_start_secs: float = Field(default=0.2, gt=0)

    # Seconds of continuous silence before Silero reports "speech ended".
    # Raise when speaker pauses are long.
    vad_stop_secs: float = Field(default=0.7, gt=0)

    # Speech is detected when Silero's speech probability >= vad_confidence, and
    # loudness (0-1, Pipecat's scale) >= vad_min_volume. Raise them to suppress
    # false starts from a sensitive mic or crowd noise. min_volume is the riskier
    # one, since quiet speakers can fall below it. Requires tuning at the venue.
    vad_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    vad_min_volume: float = Field(default=0.6, ge=0.0, le=1.0)

    # Max continuous-speech duration before a forced STT segment flush (batch
    # STT only). Prevents indefinite buffering when a speaker never pauses long
    # enough to trigger VAD silence detection. Set to 0 to disable. This is the
    # default for the dashboard's "Max segment" dial, which overrides it per
    # session.
    stt_max_segment_secs: float = Field(default=8.0, ge=0)

    # ── Translation context ───────────────────────────────────────────────────
    # How many previous utterances to include in the context for translation.
    rolling_window_n: int = Field(default=4, ge=0)

    # ── Session ───────────────────────────────────────────────────────────────
    # Set in sea-cast.yaml (see ConfigFile).
    system_prompt_path: Path = Path("app/prompts/system_prompt.md")

    # ── Terminology (optional) ─────────────────────────────────────────────────
    # Set in sea-cast.yaml (see ConfigFile). None turns the glossary off.
    glossary: GlossaryConfig | None = None

    # ── Transport (WebSocket) ─────────────────────────────────────────────────
    # WebSocket session idle timeout in seconds. None = no timeout (rely on
    # continuous mic audio to keep the connection alive).
    ws_session_timeout_secs: int | None = None

    # When a session stops: wait transcription_drain_secs (always) for the last
    # transcript to arrive, then up to translation_drain_secs for in-flight
    # translations to finish (returns as soon as they do), then close.
    transcription_drain_secs: float = Field(default=0.5, ge=0)
    translation_drain_secs: float = Field(default=15.0, gt=0)

    # ── Rooms / audience delivery ─────────────────────────────────────────────
    # Audience token lifetime. 6h comfortably covers a conference session.
    # Also used for admin-history login tokens (see below).
    jwt_ttl_secs: int = Field(default=21600, gt=0)

    # Admin gate for the session-history endpoints (GET /sessions*).
    admin_username: str | None = None
    admin_password: str | None = None

    # Room-state TTL: a room is deleted this long after it's created (12h), so
    # abandoned rooms never accumulate. Cut to ~10 min once its session ends.
    room_ttl_secs: int = Field(default=43200, gt=0)

    # Per-language caption replay buffer (most captions kept) and how far back
    # a (re)connecting audience client is caught up. Both match what an
    # audience phone keeps on screen (MAX_LINES / MAX_AGE_MS in the frontend's
    # captionFeed.ts), so a late joiner sees what everyone else does. Change
    # them together.
    caption_history_maxlen: int = Field(default=200, gt=0)
    caption_replay_secs: int = Field(default=90, ge=0)

    # Cap on number of target languages for a single session to avoid overloading
    # translator services.
    max_target_languages: int = Field(default=5, ge=1)

    # ── Server ─────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=list)

    # Directory of a built frontend bundle (`npm run build`) to serve at "/", so
    # one server hosts the app and the API. When None (the default), the backend
    # is API-only and the Vite dev server serves the UI.
    static_dir: Path | None = None

    @model_validator(mode="after")
    def _require_profiles(self) -> Settings:
        """Each profile list is non-empty, with unique ids."""
        for name, profiles in (
            ("stt_profiles", self.stt_profiles),
            ("translator_profiles", self.translator_profiles),
        ):
            if not profiles:
                raise ValueError(
                    f"{name} must list at least one profile "
                    "(see sea-cast.example.yaml)."
                )
            ids = [p.id for p in profiles]
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"{name} has duplicate ids: {duplicates}")
        return self

    @model_validator(mode="after")
    def _require_glossary_file_when_set(self) -> Settings:
        """Fail at startup when glossary.path names a file that does not exist."""
        if self.glossary is not None and not self.glossary.path.is_file():
            raise ValueError(
                f"glossary.path points at {self.glossary.path}, which does not exist"
            )
        return self

    def load_system_prompt(self) -> str:
        """Read the translation system prompt from ``system_prompt_path``.

        Returns:
            The prompt without surrounding whitespace, with ``{language}`` still to
            be filled in per language.

        Raises:
            ConfigError: The file doesn't exist, or doesn't contain ``{language}``.
        """
        try:
            prompt = self.system_prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as e:
            raise ConfigError(
                f"System prompt file not found at {self.system_prompt_path}. "
                "Set system_prompt_path in the config file (SEA_CAST_CONFIG) or "
                "create the file."
            ) from e
        if LANGUAGE_PLACEHOLDER not in prompt:
            raise ConfigError(
                f"The system prompt at {self.system_prompt_path} must contain "
                f"{LANGUAGE_PLACEHOLDER}, where the target language's name goes."
            )
        return prompt

    def resolve_stt_profile(self, profile_id: str | None) -> SttProfile:
        """Return the STT profile with ``profile_id`` (the first one when unset).

        Args:
            profile_id: The session's ``stt_profile`` (None or "" for the default).

        Returns:
            The matching profile, or the first (default) one.

        Raises:
            ValueError: No profile has that id.
        """
        if not profile_id:
            return self.stt_profiles[0]

        for profile in self.stt_profiles:
            if profile.id == profile_id:
                return profile

        raise ValueError(
            f"stt_profile must be one of {[p.id for p in self.stt_profiles]}, "
            f"got {profile_id!r}"
        )

    def resolve_translator(self, translator_id: str | None) -> TranslatorProfile:
        """Return the translator profile with ``translator_id`` (the first when unset).

        Args:
            translator_id: The session's ``translator`` (None or "" for the default).

        Returns:
            The matching profile, or the first (default) one.

        Raises:
            ValueError: No profile has that id.
        """
        if not translator_id:
            return self.translator_profiles[0]

        for profile in self.translator_profiles:
            if profile.id == translator_id:
                return profile

        raise ValueError(
            f"translator must be one of {[p.id for p in self.translator_profiles]}, "
            f"got {translator_id!r}"
        )

    def resolve_translator_api_key(self, profile: TranslatorProfile) -> str:
        """Get the API key for a profile (its named env var, or ``"none"``).

        ``"none"`` is a harmless placeholder for keyless endpoints (vLLM /
        llama.cpp ignore it) and the OpenAI client just requires a non-empty string.
        ``load_settings`` has already checked that a named var is set.

        Args:
            profile: The translator profile.

        Returns:
            The key from the env var ``api_key_env`` names, or ``"none"``.
        """
        if profile.api_key_env:
            key = os.environ.get(profile.api_key_env)
            if key:
                return key
        return "none"


class ConfigError(Exception):
    """Config file is missing or invalid, or a setting is in the wrong place."""


class ConfigFile(BaseModel):
    """The schema of the config YAML file. Unknown keys are rejected.

    Its fields can only be set in this file: ``load_settings`` fails if one is also
    set in the environment.

    Attributes:
        stt_profiles: The STT profiles (OpenAISttProfile or ElevenLabsSttProfile,
            picked by ``adapter``).
        translator_profiles: The translator profiles.
        system_prompt_path: The translation system prompt file. (Optional)
        glossary: The glossary to use. (Optional; omitted turns it off)
    """

    model_config = ConfigDict(extra="forbid")

    stt_profiles: list[SttProfile]
    translator_profiles: list[TranslatorProfile]
    system_prompt_path: Path | None = None
    glossary: GlossaryConfig | None = None


def load_config_file(path: Path) -> dict[str, Any]:
    """Read and validate the config YAML file.

    Args:
        path: The config file.

    Returns:
        The settings the file sets, keyed by ``Settings`` field name.

    Raises:
        ConfigError: The file is missing, isn't a YAML mapping, or fails validation.
    """
    # File exists/can be read.
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise ConfigError(
            f"Config file {path} not found. Copy sea-cast.example.yaml to {path}, "
            "or point SEA_CAST_CONFIG at your config file."
        ) from e

    # File can be loaded by YAML.
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} is not valid YAML: {e}") from e

    # Loaded data is in dictionary form.
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping of settings")

    # Validate config.
    try:
        config = ConfigFile.model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"Invalid {path}: {e}") from e

    # Return only the keys in the YAML file (Settings uses defaults for the rest).
    return {
        name: getattr(config, name)
        for name in config.model_fields_set
        if getattr(config, name) is not None
    }


def load_settings(config_path: Path) -> Settings:
    """Build ``Settings`` from ``config_path`` plus the environment.

    Args:
        config_path: The config file (``sea-cast.yaml`` unless SEA_CAST_CONFIG
            names another).

    Returns:
        The validated settings.

    Raises:
        ConfigError: The file is invalid, a file-only setting is also set in the
            environment (e.g. a leftover ``TRANSLATOR_PROFILES`` in ``.env``), a
            setting fails validation, or an env var a profile names for its
            credentials isn't set.
    """
    file_values = load_config_file(config_path)

    # Ensure no duplicate fields in environment and config file.
    in_env = sorted(
        name.upper() for name in ConfigFile.model_fields if name.upper() in os.environ
    )
    if in_env:
        raise ConfigError(
            f"{', '.join(in_env)} must be set in {config_path}, not the environment. "
            "Remove it from .env."
        )

    try:
        settings = Settings(**file_values)
    except ValidationError as e:
        raise ConfigError(
            f"Invalid settings from {config_path} or the environment: {e}"
        ) from e

    _check_stt_profiles(settings.stt_profiles, config_path)
    _check_translator_profiles(settings.translator_profiles, config_path)
    return settings


def _require_env(location: str, names: list[str], config_path: Path) -> None:
    """Fail when an env var a profile names for its credentials is unset or empty.

    Args:
        location: The profile, for the error message, e.g. "stt_profiles 'scribe'".
        names: The env vars the profile names (an API key, or a username and
            password).
        config_path: The config file, for the error message.

    Raises:
        ConfigError: Naming the profile and each missing var.
    """
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ConfigError(
            f"{location} in {config_path} needs {', '.join(missing)}, which is not "
            "set. Add it to .env."
        )


def _check_stt_profiles(profiles: list[SttProfile], config_path: Path) -> None:
    """Fail on a missing secret; warn on guessed languages or credentials over HTTP.

    Args:
        profiles: The loaded STT profiles.
        config_path: The config file, for error messages.

    Raises:
        ConfigError: An env var named by a profile's ``auth`` is unset or empty.
    """
    for profile in profiles:
        location = f"stt_profiles {profile.id!r}"

        # Ensure auth env vars are set.
        if profile.auth is not None:
            names = (
                [profile.auth.api_key_env]
                if isinstance(profile.auth, BearerAuth)
                else [profile.auth.username_env, profile.auth.password_env]
            )
            _require_env(location, names, config_path)

            # Warn if credentials would go over plain HTTP.
            if isinstance(profile, OpenAISttProfile):
                _warn_if_plain_http(location, profile.base_url)

        if profile.languages is None:
            logger.warning(
                "{}: no languages set, so every app language is offered and "
                "auto-detect is off. Set languages to a preset from languages.yaml's "
                "stt_models, or list them inline: {{recognizes: [...]}}.",
                location,
            )


def _check_translator_profiles(
    profiles: list[TranslatorProfile], config_path: Path
) -> None:
    """Check that API keys are set and warn on credentials over HTTP.

    Args:
        profiles: The loaded translator profiles.
        config_path: The config file, for error messages.

    Raises:
        ConfigError: A profile's ``api_key_env`` names an unset or empty var.
    """
    for profile in profiles:
        if profile.api_key_env:
            location = f"translator_profiles {profile.id!r}"
            _require_env(location, [profile.api_key_env], config_path)
            _warn_if_plain_http(location, profile.base_url)


def _warn_if_plain_http(location: str, base_url: str) -> None:
    """Warn when credentials would go over plain HTTP to another machine.

    Localhost and other loopback addresses are fine.

    Args:
        location: The profile, for the warning, e.g. "stt_profiles 'scribe'".
        base_url: The profile's base URL.
    """
    url = urlsplit(base_url)
    if url.scheme == "http" and url.hostname not in _LOCAL_HOSTS:
        logger.warning(
            "{} sends credentials over plain http:// to {}; anyone on the network "
            "path can read them. Use https://.",
            location,
            url.hostname,
        )


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance.

    Returns:
        A ``Settings`` object that draws from the environment
        and the YAML config file.
    """
    load_dotenv(".env", override=False)
    config = Path(os.environ.get("SEA_CAST_CONFIG") or DEFAULT_CONFIG_PATH)
    return load_settings(config)
