"""Rooms: what one speaker session shares with its audience, plus join tokens.

A room holds the session's metadata, caption history (for replay) and listener
registry, keyed by its 6-character code, so rooms never see each other's data.

- ``RoomStore``: the storage interface. ``MemoryRoomStore`` keeps rooms in this
  process's memory, so one server process must hold every connection, and a
  restart drops live rooms (the audience re-scans the QR).
- ``RoomManager``: creates rooms with unique codes, attaches the speaker's
  session, and issues and checks the short-lived audience tokens.

A room is deleted ``room_ttl_secs`` after it's created, or about 10 minutes after
its session ends.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal, Protocol

import jwt

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

RoomStatus = Literal["created", "live", "ended"]

# Language code for the untranslated source transcript. It's always published
# (the stage monitor shows it), and offered to the audience only when the room
# has include_source.
SOURCE_LANG = "source"

# ``aud`` claim in audience tokens — rejects any other JWT that happens to be
# signed with the same secret.
JWT_AUDIENCE = "sea-cast-audience"

# The server sends each audience socket *something* at least this often (a ping
# when captions are idle), and renews the listener in the registry at the same
# pace. The audience app's STALE_TIMEOUT_MS (frontend/src/lib/captionsSocket.ts,
# 45s) must stay above twice this, so a healthy socket survives a missed ping.
PING_INTERVAL_SECS = 20

# A listener counts as live if renewed within this window: three pings, so a
# live listener survives two missed beats.
LISTENER_FRESH_SECS = 3 * PING_INTERVAL_SECS

# How long an ended room lingers: long enough for late listeners to get
# "session ended", short enough to free the code.
ENDED_ROOM_TTL_SECS = 600

# Crockford base32 — no I/L/O/U, so codes survive being read aloud or retyped.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LEN = 6
_CODE_ATTEMPTS = 8

# Epoch-seconds clock, injectable for tests. (JWT ``exp`` verification inside
# pyjwt always uses the wall clock regardless.)
type Clock = Callable[[], float]


def _now_ms(clock: Clock) -> int:
    """Return the clock's time in epoch milliseconds."""
    return int(clock() * 1000)


def _generate_code() -> str:
    """Return a random room code (6 characters, Crockford base32)."""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))


@dataclass(frozen=True)
class RoomMetadata:
    """A room's metadata.

    Attributes:
        room_id: The room's code.
        status: "created", then "live" once the speaker connects, then "ended".
        languages: The caption languages offered to the audience.
        input_language: The speaker's source language code (or "auto").
        created_at: When the room was created, in epoch milliseconds.
        session_id: The speaker session attached to it, once live.
        stt_profile: The attached session's STT profile id.
        include_source: Whether the untranslated transcript is offered as its
            own caption channel (the "source" language).
    """

    room_id: str
    status: RoomStatus
    languages: list[str]
    input_language: str
    created_at: int
    session_id: str | None = None
    stt_profile: str | None = None
    include_source: bool = False


@dataclass(frozen=True)
class CaptionEntry:
    """One final caption in a room's per-language replay history.

    Attributes:
        utterance_id: The utterance it belongs to.
        text: The final caption text.
        sent_at_ms: When it was published, in epoch milliseconds.
    """

    utterance_id: str
    text: str
    sent_at_ms: int


@dataclass(frozen=True)
class IssuedToken:
    """A join token and when it expires.

    Attributes:
        token: The signed JWT.
        expires_at: Its expiry, in epoch milliseconds (for the client).
    """

    token: str
    expires_at: int


class TokenError(Exception):
    """A token was rejected: invalid, expired, or for another room."""


class RoomStore(Protocol):
    """Storage interface for rooms.

    Every method is safe to call for an unknown or expired room: writes do
    nothing, and reads return None, [] or zero counts.
    """

    async def create(self, metadata: RoomMetadata) -> bool:
        """Store a new room.

        Args:
            metadata: The room's metadata.

        Returns:
            True, or False (nothing stored) if the code is already in use.
        """
        ...

    async def get(self, room_id: str) -> RoomMetadata | None:
        """Return a room's metadata.

        Args:
            room_id: The room's code.

        Returns:
            The metadata, or None for an unknown or expired room.
        """
        ...

    async def attach_session(
        self, room_id: str, *, session_id: str, stt_profile: str
    ) -> bool:
        """Attach the speaker's session and mark the room live.

        Args:
            room_id: The room's code.
            session_id: The speaker session's id.
            stt_profile: The session's STT profile id.

        Returns:
            True, or False for an unknown room.
        """
        ...

    async def room_for_session(self, session_id: str) -> str | None:
        """Return the room a speaker session is attached to.

        Args:
            session_id: The speaker session's id.

        Returns:
            The room's code, or None.
        """
        ...

    async def end_room(self, room_id: str) -> None:
        """Mark the room ended and shorten its remaining lifetime; safe to repeat.

        Args:
            room_id: The room's code.
        """
        ...

    async def append_history(
        self, room_id: str, lang: str, entry: CaptionEntry
    ) -> None:
        """Add a final caption to the room's replay history (oldest drop off).

        Args:
            room_id: The room's code.
            lang: The caption language.
            entry: The caption.
        """
        ...

    async def read_history(
        self, room_id: str, lang: str, *, since_ms: int
    ) -> list[CaptionEntry]:
        """Return the replay history from a point in time.

        Args:
            room_id: The room's code.
            lang: The caption language.
            since_ms: The earliest ``sent_at_ms`` to include.

        Returns:
            The captions sent at or after ``since_ms``, oldest first.
        """
        ...

    async def touch_listener(self, room_id: str, lang: str, conn_id: str) -> None:
        """Register an audience connection, or renew it (its heartbeat).

        Args:
            room_id: The room's code.
            lang: The language it listens to.
            conn_id: The connection's id.
        """
        ...

    async def remove_listener(self, room_id: str, lang: str, conn_id: str) -> None:
        """Remove an audience connection.

        Args:
            room_id: The room's code.
            lang: The language it listened to.
            conn_id: The connection's id.
        """
        ...

    async def listener_counts(
        self, room_id: str, langs: Sequence[str]
    ) -> dict[str, int]:
        """Count live listeners per language.

        Args:
            room_id: The room's code.
            langs: The languages to count.

        Returns:
            Per language, the connections renewed within LISTENER_FRESH_SECS.
        """
        ...


# ──────────────────────────────────────────────────────────────────────────
# In-memory backend
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class _MemoryRoom:
    """One room as MemoryRoomStore holds it."""

    metadata: RoomMetadata
    expires_at: float  # epoch seconds
    history: dict[str, deque[CaptionEntry]] = field(default_factory=dict)
    # lang → {conn_id: last renewed, epoch ms}
    listeners: dict[str, dict[str, int]] = field(default_factory=dict)


class MemoryRoomStore:
    """Single-process ``RoomStore``: every room in this process's memory.

    Expiry is lazy: a room past its deadline is dropped the next time any
    method looks it up.
    """

    def __init__(
        self,
        *,
        room_ttl_secs: int = 43200,
        ended_ttl_secs: int = ENDED_ROOM_TTL_SECS,
        history_maxlen: int = 200,
        clock: Clock = time.time,
    ) -> None:
        """Create an empty store.

        Args:
            room_ttl_secs: How long a room lives after it's created.
            ended_ttl_secs: How long an ended room lingers.
            history_maxlen: The most final captions kept per language for replay.
            clock: The current time in epoch seconds (injectable for tests).
        """
        self._room_ttl_secs = room_ttl_secs
        self._ended_ttl_secs = ended_ttl_secs
        self._history_maxlen = history_maxlen
        self._clock = clock
        self._rooms: dict[str, _MemoryRoom] = {}
        self._sessions: dict[str, str] = {}

    def _room(self, room_id: str) -> _MemoryRoom | None:
        """Return a room, dropping it first if it has expired."""
        room = self._rooms.get(room_id)
        if room is None:
            return None
        if room.expires_at <= self._clock():
            del self._rooms[room_id]
            if room.metadata.session_id is not None:
                self._sessions.pop(room.metadata.session_id, None)
            return None
        return room

    async def create(self, metadata: RoomMetadata) -> bool:
        """Store a new room, unless its code is in use."""
        if self._room(metadata.room_id) is not None:
            return False
        self._rooms[metadata.room_id] = _MemoryRoom(
            metadata=metadata, expires_at=self._clock() + self._room_ttl_secs
        )
        return True

    async def get(self, room_id: str) -> RoomMetadata | None:
        """Return a room's metadata, or None."""
        room = self._room(room_id)
        return room.metadata if room is not None else None

    async def attach_session(
        self, room_id: str, *, session_id: str, stt_profile: str
    ) -> bool:
        """Attach the speaker's session and mark the room live."""
        room = self._room(room_id)
        if room is None:
            return False
        room.metadata = replace(
            room.metadata, status="live", session_id=session_id, stt_profile=stt_profile
        )
        self._sessions[session_id] = room_id
        return True

    async def room_for_session(self, session_id: str) -> str | None:
        """Return the room a session is attached to, or None."""
        room_id = self._sessions.get(session_id)
        if room_id is None or self._room(room_id) is None:
            return None
        return room_id

    async def end_room(self, room_id: str) -> None:
        """Mark the room ended and shorten its lifetime."""
        room = self._room(room_id)
        if room is None:
            return
        room.metadata = replace(room.metadata, status="ended")
        room.expires_at = min(room.expires_at, self._clock() + self._ended_ttl_secs)

    async def append_history(
        self, room_id: str, lang: str, entry: CaptionEntry
    ) -> None:
        """Add a final caption to the replay history."""
        room = self._room(room_id)
        if room is None:
            return
        room.history.setdefault(lang, deque(maxlen=self._history_maxlen)).append(entry)

    async def read_history(
        self, room_id: str, lang: str, *, since_ms: int
    ) -> list[CaptionEntry]:
        """Return the captions sent at or after ``since_ms``."""
        room = self._room(room_id)
        if room is None:
            return []
        return [e for e in room.history.get(lang, ()) if e.sent_at_ms >= since_ms]

    async def touch_listener(self, room_id: str, lang: str, conn_id: str) -> None:
        """Register or renew an audience connection."""
        room = self._room(room_id)
        if room is None:
            return
        room.listeners.setdefault(lang, {})[conn_id] = _now_ms(self._clock)

    async def remove_listener(self, room_id: str, lang: str, conn_id: str) -> None:
        """Remove an audience connection."""
        room = self._room(room_id)
        if room is None:
            return
        room.listeners.get(lang, {}).pop(conn_id, None)

    async def listener_counts(
        self, room_id: str, langs: Sequence[str]
    ) -> dict[str, int]:
        """Count live listeners per language."""
        room = self._room(room_id)
        if room is None:
            return dict.fromkeys(langs, 0)
        bound = _now_ms(self._clock) - LISTENER_FRESH_SECS * 1000
        return {
            lang: sum(
                1
                for renewed_ms in room.listeners.get(lang, {}).values()
                if renewed_ms > bound
            )
            for lang in langs
        }


# ──────────────────────────────────────────────────────────────────────────
# Manager — code generation, listeners, audience JWTs
# ──────────────────────────────────────────────────────────────────────────


class RoomManager:
    """What the server uses for rooms: creating them, and audience tokens.

    Plain storage goes through ``.store``; this class adds code allocation,
    listener ids and token signing.

    Attributes:
        store: The room store.
    """

    def __init__(
        self,
        store: RoomStore,
        *,
        jwt_secret: str | None = None,
        jwt_ttl_secs: int = 21600,
        clock: Clock = time.time,
    ) -> None:
        """Bind the manager to a store.

        Args:
            store: The room store.
            jwt_secret: The token signing secret; None generates one for this
                process.
            jwt_ttl_secs: How long audience tokens are valid.
            clock: The current time in epoch seconds (injectable for tests).
        """
        self.store = store
        self._jwt_ttl_secs = jwt_ttl_secs
        self._clock = clock
        # A per-process secret is enough: rooms live in this process's memory, so
        # their tokens never need to outlive it.
        self._jwt_secret = jwt_secret or secrets.token_hex(32)

    async def create_room(
        self,
        *,
        languages: Sequence[str],
        input_language: str,
        include_source: bool = False,
    ) -> RoomMetadata:
        """Create a room with a new unique code (status ``created``).

        Args:
            languages: The caption languages to offer.
            input_language: The speaker's source language code (or "auto").
            include_source: Whether to offer the untranslated transcript too.

        Returns:
            The new room's metadata.

        Raises:
            RuntimeError: No unused code was found.
        """
        for _ in range(_CODE_ATTEMPTS):
            metadata = RoomMetadata(
                room_id=_generate_code(),
                status="created",
                languages=list(languages),
                input_language=input_language,
                created_at=_now_ms(self._clock),
                include_source=include_source,
            )
            if await self.store.create(metadata):
                return metadata
        raise RuntimeError("could not allocate a unique room code")

    async def register_listener(self, room_id: str, lang: str) -> str:
        """Register a new audience connection.

        Args:
            room_id: The room's code.
            lang: The language it listens to.

        Returns:
            The connection's id, for its heartbeats.
        """
        conn_id = uuid.uuid4().hex
        await self.store.touch_listener(room_id, lang, conn_id)
        return conn_id

    async def listener_counts(self, room_id: str) -> dict[str, int]:
        """Count live listeners for each of the room's languages.

        Args:
            room_id: The room's code.

        Returns:
            Per language, the live listeners. {} for an unknown room.
        """
        metadata = await self.store.get(room_id)
        if metadata is None:
            return {}
        return await self.store.listener_counts(room_id, metadata.languages)

    def issue_token(self, room_id: str) -> IssuedToken:
        """Create a short-lived audience token for one room.

        It names no language on purpose, so the audience can switch languages
        without re-joining; the captions socket checks the language itself.

        Args:
            room_id: The room's code.

        Returns:
            The token and its expiry.
        """
        iat = int(self._clock())
        exp = iat + self._jwt_ttl_secs
        claims = {
            "room_id": room_id,
            "aud": JWT_AUDIENCE,
            "iat": iat,
            "exp": exp,
            "jti": uuid.uuid4().hex,
        }
        token = jwt.encode(claims, self._jwt_secret, algorithm="HS256")
        return IssuedToken(token=token, expires_at=exp * 1000)

    def verify_token(self, token: str, room_id: str) -> dict[str, Any]:
        """Check an audience token's signature, audience, expiry and room.

        Args:
            token: The token.
            room_id: The room it must be for.

        Returns:
            The token's claims.

        Raises:
            TokenError: The token is invalid, expired, or for another room (the
                captions socket closes with 4001, so the client re-joins).
        """
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._jwt_secret,
                algorithms=["HS256"],
                audience=JWT_AUDIENCE,
            )
        except jwt.InvalidTokenError as e:
            raise TokenError(str(e)) from e
        if claims.get("room_id") != room_id:
            raise TokenError("token was issued for a different room")
        return claims
