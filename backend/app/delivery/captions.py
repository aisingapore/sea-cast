"""Caption fan-out: one speaker's pipeline to many audience sockets.

- ``CaptionPublisher``: what the pipeline calls to publish partial and final
  captions per language (finals also go into the room's replay history) and the
  room's status. The untranslated transcript goes out as the ``source`` language.
- ``CaptionHub``: the broadcast interface between them. ``InProcessCaptionHub``
  delivers within this process, so one server process must hold every audience
  socket, as with the room store.
- ``CaptionSubscription``: each audience socket's bounded queue. When it's full,
  the oldest partial is dropped first (a later partial or final replaces it
  anyway). If only finals are left, the subscription is marked wedged and the
  socket closes with 1013, so the client reconnects and catches up from replay.

Channels are named ``room:{code}:captions:{lang}`` and ``room:{code}:status``.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING, Protocol

from app.delivery.rooms import CaptionEntry, RoomStatus, RoomStore

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.delivery.rooms import Clock

# Per-listener buffer size. At the pipeline's real caption rate (a few
# messages per second per language) 100 messages is ~30-60s of backlog — a
# client that far behind is better served by reconnect + replay.
QUEUE_MAXSIZE = 100


def build_captions_channel(room_id: str, lang: str) -> str:
    """Return the channel name for a room's captions in one language.

    Args:
        room_id: The room's code.
        lang: The caption language (or ``source``).

    Returns:
        ``room:{room_id}:captions:{lang}``.
    """
    return f"room:{room_id}:captions:{lang}"


def build_status_channel(room_id: str) -> str:
    """Return the channel name for a room's status messages.

    Args:
        room_id: The room's code.

    Returns:
        ``room:{room_id}:status``.
    """
    return f"room:{room_id}:status"


# ── Wire-message builders (the pinned WS contract) ─────────────────────────


def build_caption_message(
    *,
    lang: str,
    utterance_id: str,
    text: str,
    final: bool,
    sequence: int,
    sent_at_ms: int,
    replay: bool = False,
) -> dict[str, object]:
    """Build a caption message, as sent to audience sockets.

    Args:
        lang: The caption language (or ``source``).
        utterance_id: The utterance it belongs to (partials and the final share it).
        text: The caption text (a partial carries the whole text so far).
        final: Whether this is the utterance's final text.
        sequence: Increases with each message for the utterance, so the client can
            ignore late ones.
        sent_at_ms: When it was published, in epoch milliseconds.
        replay: Whether it's resent from the replay history on (re)connect.

    Returns:
        The message as a JSON-serialisable dict.
    """
    return {
        "type": "caption",
        "lang": lang,
        "text": text,
        "final": final,
        "utterance_id": utterance_id,
        "seq": sequence,
        "ts": sent_at_ms,
        "replay": replay,
    }


def build_status_message(
    status: RoomStatus,
    languages: Sequence[str],
    listeners: Mapping[str, int] | None,
    *,
    sent_at_ms: int,
) -> dict[str, object]:
    """Build a room status message.

    Args:
        status: The room's status ("created", "live" or "ended").
        languages: The room's caption languages.
        listeners: Live listeners per language, or None to leave them out.
        sent_at_ms: When it was published, in epoch milliseconds.

    Returns:
        The message as a JSON-serialisable dict.
    """
    message: dict[str, object] = {
        "type": "room_status",
        "status": status,
        "languages": list(languages),
        "ts": sent_at_ms,
    }
    if listeners is not None:
        message["listeners"] = dict(listeners)
    return message


def build_ping_message(sent_at_ms: int) -> dict[str, object]:
    """Build the keep-alive message sent when captions are idle.

    Args:
        sent_at_ms: When it was sent, in epoch milliseconds.

    Returns:
        The message as a JSON-serialisable dict.
    """
    return {"type": "ping", "ts": sent_at_ms}


# ── Subscription buffer ────────────────────────────────────────────────────


class CaptionSubscription:
    """Bounded queue between the hub and one audience WebSocket.

    The hub adds messages with ``_offer``, which never blocks. The socket's handler
    awaits ``get``.

    Attributes:
        channels: The channels this subscription receives.
        wedged: True once a final or status message had to be dropped: the client
            is too far behind to catch up without loss, so its handler closes the
            socket with 1013.
    """

    def __init__(self, channels: tuple[str, ...], maxsize: int = QUEUE_MAXSIZE):
        """Create an empty subscription.

        Args:
            channels: The channels to receive.
            maxsize: The most messages held before older ones are dropped.
        """
        self.channels = channels
        self.wedged = False
        self._maxsize = maxsize
        self._items: deque[dict[str, object]] = deque()
        self._wakeup = asyncio.Event()

    def _offer(self, message: dict[str, object]) -> None:
        """Queue a message, dropping an older one if the queue is full.

        Args:
            message: The message to queue.
        """
        if len(self._items) >= self._maxsize:
            # Drop the oldest partial: it has already been superseded by a
            # later partial or final sitting behind it in this same buffer.
            for i, queued in enumerate(self._items):
                if queued.get("final", True) is False:
                    del self._items[i]
                    break
            else:  # nothing disposable left — dropping loses real data
                self._items.popleft()
                self.wedged = True
        self._items.append(message)
        self._wakeup.set()

    async def get(self) -> dict[str, object]:
        """Wait for the next message.

        Returns:
            The oldest queued message.
        """
        while not self._items:
            self._wakeup.clear()
            await self._wakeup.wait()
        return self._items.popleft()


class CaptionHub(Protocol):
    """Publish/subscribe interface for caption fan-out."""

    async def start(self) -> None:
        """Start the hub (called once, at server startup)."""
        ...

    async def stop(self) -> None:
        """Stop the hub (called once, at server shutdown)."""
        ...

    async def publish(self, channel: str, message: dict[str, object]) -> None:
        """Deliver a message to every subscription on the channel.

        Args:
            channel: The channel to publish on.
            message: The message to deliver.
        """
        ...

    async def subscribe(self, channels: Sequence[str]) -> CaptionSubscription:
        """Subscribe to channels.

        Args:
            channels: The channels to receive.

        Returns:
            A new subscription for those channels.
        """
        ...

    async def unsubscribe(self, subscription: CaptionSubscription) -> None:
        """Stop delivering to a subscription.

        Args:
            subscription: The subscription to remove.
        """
        ...


# ── In-process hub ─────────────────────────────────────────────────────────


class InProcessCaptionHub:
    """``CaptionHub`` that delivers straight to this process's subscriptions.

    No broker and no background task, so it's correct while one server process
    holds the publishing pipeline and every audience socket.
    """

    def __init__(self) -> None:
        """Create a hub with no subscriptions."""
        self._subs: dict[str, set[CaptionSubscription]] = {}

    async def start(self) -> None:
        """Nothing to start; part of the hub interface."""
        return

    async def stop(self) -> None:
        """Nothing to stop; part of the hub interface."""
        return

    async def publish(self, channel: str, message: dict[str, object]) -> None:
        """Queue a copy of the message on every subscription to the channel.

        Args:
            channel: The channel to publish on.
            message: The message to deliver.
        """
        for sub in tuple(self._subs.get(channel, ())):
            sub._offer(dict(message))

    async def subscribe(self, channels: Sequence[str]) -> CaptionSubscription:
        """Subscribe to channels.

        Args:
            channels: The channels to receive.

        Returns:
            A new subscription for those channels.
        """
        sub = CaptionSubscription(tuple(channels))
        for channel in sub.channels:
            self._subs.setdefault(channel, set()).add(sub)
        return sub

    async def unsubscribe(self, subscription: CaptionSubscription) -> None:
        """Stop delivering to a subscription.

        Args:
            subscription: The subscription to remove.
        """
        for channel in subscription.channels:
            subs = self._subs.get(channel)
            if subs is None:
                continue
            subs.discard(subscription)
            if not subs:
                del self._subs[channel]


# ── Publisher facade (what the pipeline talks to) ──────────────────────────


class CaptionPublisher:
    """Publishes one room's captions and status; the pipeline holds one per room.

    Finals are also added to the room's replay history, so late joiners and
    reconnects catch up. The untranslated transcript is published by passing
    ``rooms.SOURCE_LANG`` as the language.
    """

    def __init__(
        self,
        hub: CaptionHub,
        store: RoomStore,
        room_id: str,
        *,
        languages: Sequence[str],
        clock: Clock = time.time,
    ) -> None:
        """Bind the publisher to a room.

        Args:
            hub: The caption hub to publish through.
            store: The room store (for the replay history and listener counts).
            room_id: The room's code.
            languages: The room's caption languages.
            clock: The current time in epoch seconds (injectable for tests).
        """
        self._hub = hub
        self._store = store
        self._room_id = room_id
        self._languages = list(languages)
        self._clock = clock

    def _now_ms(self) -> int:
        """Return the current time in epoch milliseconds."""
        return int(self._clock() * 1000)

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
        await self._hub.publish(
            build_captions_channel(self._room_id, lang),
            build_caption_message(
                lang=lang,
                utterance_id=utterance_id,
                text=text,
                final=False,
                sequence=sequence,
                sent_at_ms=self._now_ms(),
            ),
        )

    async def publish_final(
        self, lang: str, utterance_id: str, text: str, sequence: int
    ) -> None:
        """Publish a final caption, and add it to the replay history.

        Args:
            lang: The caption language.
            utterance_id: The utterance it belongs to.
            text: The final text.
            sequence: The message's number within the utterance.
        """
        sent_at_ms = self._now_ms()
        await self._store.append_history(
            self._room_id,
            lang,
            CaptionEntry(utterance_id=utterance_id, text=text, sent_at_ms=sent_at_ms),
        )
        await self._hub.publish(
            build_captions_channel(self._room_id, lang),
            build_caption_message(
                lang=lang,
                utterance_id=utterance_id,
                text=text,
                final=True,
                sequence=sequence,
                sent_at_ms=sent_at_ms,
            ),
        )

    async def publish_status(self, status: RoomStatus) -> None:
        """Publish the room's status, with its current listener counts.

        Args:
            status: The room's status ("created", "live" or "ended").
        """
        listeners = await self._store.listener_counts(self._room_id, self._languages)
        await self._hub.publish(
            build_status_channel(self._room_id),
            build_status_message(
                status, self._languages, listeners, sent_at_ms=self._now_ms()
            ),
        )
