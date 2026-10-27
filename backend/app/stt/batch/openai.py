"""Batch STT service for any OpenAI-compatible ``POST /audio/transcriptions`` endpoint.

The standard OpenAI multipart upload schema is widely supported, including local
whisper.cpp, vLLM and OpenRouter API. ``OpenAICompatibleBatchSTT`` is introduced here
as an extension of Pipecat's ``OpenAISTTService`` and allows the use of all these
OpenAI-compatible endpoints.

It reuses the whole ``SegmentedSTTService`` machinery (VAD segmentation, resampling,
WAV framing, metrics, ``TranscriptionFrame`` emission), with just two changes:

- **Auth**, chosen in ``_create_client``: HTTP Basic when a username is set, else
  Bearer when an API key is set, else none.
- **Error handling**, in ``_transcribe``: a HTTP error or an unreachable endpoint is
  logged and surfaced to the dashboard as an ``stt_error`` RTVI server message. This
  results in an empty transcript, so the session can continue to run.

Requests are never retried since a retry with backoff during live captioning could
potentially block many subsequent utterances.
"""

import base64

import openai
from loguru import logger
from openai import AsyncOpenAI
from openai.types.audio import Transcription
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.services.openai.stt import OpenAISTTService, OpenAISTTSettings

# Default per-request timeout (the SDK's own default is 10 minutes).
REQUEST_TIMEOUT_SECS = 15.0

# Keyless servers (a local whisper.cpp) ignore the Bearer header, but the OpenAI SDK
# refuses to build a client without some key.
_PLACEHOLDER_KEY = "none"


class OpenAICompatibleBatchSTT(OpenAISTTService):
    """``OpenAISTTService`` with auth and dashboard-visible errors."""

    def __init__(
        self,
        *,
        profile_id: str,
        profile_label: str,
        base_url: str,
        settings: OpenAISTTSettings,
        api_key: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout_secs: float = REQUEST_TIMEOUT_SECS,
    ) -> None:
        """Build the service for an OpenAI-compatible endpoint.

        Credentials (Basic/Bearer) should only be used when ``base_url``
        is HTTPS because they are readable in transit.

        Args:
            profile_id: The STT profile id, used in logs.
            profile_label: The STT profile's label, shown in dashboard errors.
            base_url: The API root path, e.g. ``http://localhost:8000/v1``.
            settings: The model and spoken language sent with every request.
            api_key: Sent as a Bearer token when set and no username is given.
            username: Username for Basic auth.
            password: Password for Basic auth.
            timeout_secs: Per-request timeout; a slower request is dropped.
        """
        self._profile_id = profile_id
        self._profile_label = profile_label
        self._timeout_secs = timeout_secs
        # Set before super().__init__: the base constructor calls _create_client.
        self._basic_auth_header = (
            "Basic "
            + base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            if username
            else None
        )
        super().__init__(api_key=api_key, base_url=base_url, settings=settings)

    def _create_client(self, api_key: str | None, base_url: str | None) -> AsyncOpenAI:
        """Build the SDK client: this endpoint's auth, no retries, a short timeout.

        Args:
            api_key: The Bearer key, or None.
            base_url: The API root path.

        Returns:
            The client. A Basic header in ``default_headers`` replaces the Bearer
            header the SDK builds from ``api_key``, so only one is sent.
        """
        return AsyncOpenAI(
            api_key=api_key or _PLACEHOLDER_KEY,
            base_url=base_url,
            default_headers=(
                {"Authorization": self._basic_auth_header}
                if self._basic_auth_header
                else None
            ),
            max_retries=0,
            timeout=self._timeout_secs,
        )

    async def _transcribe(self, audio: bytes) -> Transcription:
        """Transcribe one VAD segment; report failures instead of raising.

        Args:
            audio: One utterance as a WAV file.

        Returns:
            The transcription, or an empty one when the request failed.
        """
        try:
            return await super()._transcribe(audio)
        except openai.APIStatusError as e:
            status = e.status_code
            if status == 429:
                message = "rate-limited (429). Retrying on the next utterance."
            elif status in (401, 403):
                message = f"auth failed ({status}). Check the credentials."
            else:
                message = f"error ({status})."
            # The response body carries the server's real error detail.
            logger.error(
                "STT {} HTTP {}: {}", self._profile_id, status, e.response.text
            )
            await self._push_stt_error(
                status_code=status,
                message=message,
                retry_after=e.response.headers.get("retry-after"),
            )
        except openai.APIConnectionError as e:  # includes APITimeoutError
            logger.error("STT {} unreachable: {!r}", self._profile_id, e)
            await self._push_stt_error(
                status_code=None,
                message=f"unreachable ({e.__class__.__name__}).",
            )
        return Transcription(text="")

    async def _push_stt_error(
        self,
        *,
        status_code: int | None,
        message: str,
        retry_after: str | None = None,
    ) -> None:
        """Send a ``stt_error`` RTVI server message for the dashboard's error banner.

        Args:
            status_code: The HTTP status, or None when there was no response.
            message: The error prefixed with the profile's label.
            retry_after: The server's ``Retry-After`` header, if any.
        """
        await self.push_frame(
            RTVIServerMessageFrame(
                data={
                    "type": "stt_error",
                    "profile_id": self._profile_id,
                    "status_code": status_code,
                    "message": f"STT ({self._profile_label}) {message}",
                    "retry_after": retry_after,
                }
            )
        )
