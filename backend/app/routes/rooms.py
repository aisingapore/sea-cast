"""Room routes: create a room, report on it, join it, and stream its captions."""

import asyncio
import contextlib
import time

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.delivery.captions import (
    CaptionSubscription,
    build_caption_message,
    build_captions_channel,
    build_ping_message,
    build_status_channel,
    build_status_message,
)
from app.delivery.rooms import PING_INTERVAL_SECS, SOURCE_LANG, RoomManager, TokenError
from app.routes.state import AppState
from app.routes.validation import (
    VALID_INPUT_LANGUAGES,
    VALID_TARGET_LANGUAGES,
    drop_source_language,
)


class _CreateRoomBody(BaseModel):
    target_languages: list[str]
    input_language: str = "en"
    # Offer the untranslated source transcript to audiences as its own channel.
    include_source: bool = False


async def _pump_captions(
    websocket: WebSocket,
    subscription: CaptionSubscription,
    room_manager: RoomManager,
    room_id: str,
    lang: str,
    conn_id: str,
) -> None:
    """Forward caption messages to one audience socket, pinging when idle.

    Runs alongside the captions endpoint's receive loop, which owns cleanup. It
    also renews this listener in the room's registry every PING_INTERVAL_SECS,
    so the live listener count stays right even while captions are flowing.

    It closes the socket itself in two cases: the room ends (a room_status
    "ended" message; close code 1000), or the client falls too far behind and
    its subscription is wedged (close code 1013; the client reconnects, and the
    replay buffer resends the captions it missed).

    Args:
        websocket: The audience member's captions socket.
        subscription: This listener's queue of messages from the caption hub.
        room_manager: Keeps the room's listener registry.
        room_id: The room being watched.
        lang: The caption language (or "source").
        conn_id: This connection's id in the listener registry.
    """
    last_beat = time.monotonic()
    try:
        while True:
            if subscription.wedged:
                await websocket.close(code=1013, reason="Client too slow")
                return
            try:
                message = await asyncio.wait_for(
                    subscription.get(), timeout=PING_INTERVAL_SECS
                )
            except TimeoutError:
                await websocket.send_json(build_ping_message(int(time.time() * 1000)))
            else:
                await websocket.send_json(message)
                if (
                    message.get("type") == "room_status"
                    and message.get("status") == "ended"
                ):
                    # Normal close (code 1000).
                    await websocket.close()
                    return
            now = time.monotonic()
            if now - last_beat >= PING_INTERVAL_SECS:
                await room_manager.store.touch_listener(room_id, lang, conn_id)
                last_beat = now
    except (WebSocketDisconnect, RuntimeError):
        # A send raced a client disconnect / socket teardown. The endpoint's
        # receive loop notices the disconnect and runs cleanup.
        return


def build_router(state: AppState) -> APIRouter:
    """Return this module's routes, bound to the app's shared state.

    Args:
        state: The app's shared state.

    Returns:
        The router, for ``FastAPI.include_router``.
    """
    settings = state.settings
    room_manager = state.room_manager
    caption_hub = state.caption_hub
    router = APIRouter()

    @router.post("/rooms", status_code=201)
    async def create_room(body: _CreateRoomBody) -> dict[str, object]:
        """Allocate a room for the upcoming session; returns its 6-char join code."""
        languages = list(dict.fromkeys(body.target_languages))  # dedupe, keep order
        if not languages:
            raise HTTPException(
                status_code=422, detail="target_languages must not be empty"
            )
        unknown = sorted(set(languages) - VALID_TARGET_LANGUAGES)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown target_languages {unknown}; "
                f"valid: {sorted(VALID_TARGET_LANGUAGES)}",
            )
        if len(languages) > settings.max_target_languages:
            raise HTTPException(
                status_code=422,
                detail=f"at most {settings.max_target_languages} target languages "
                f"per room, got {len(languages)}",
            )
        if body.input_language not in VALID_INPUT_LANGUAGES:
            raise HTTPException(
                status_code=422,
                detail=f"input_language must be one of "
                f"{sorted(VALID_INPUT_LANGUAGES)}, got {body.input_language!r}",
            )
        languages = drop_source_language(languages, body.input_language)
        metadata = await room_manager.create_room(
            languages=languages,
            input_language=body.input_language,
            include_source=body.include_source,
        )
        return {
            "room_id": metadata.room_id,
            "languages": metadata.languages,
            "include_source": metadata.include_source,
            "source_language": metadata.input_language,
        }

    @router.get("/rooms/{room_id}")
    async def room_info(room_id: str) -> dict[str, object]:
        """Room status + live listener counts (join page; dashboard 5s poll)."""
        metadata = await room_manager.store.get(room_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Room not found")
        count_langs = (
            [*metadata.languages, SOURCE_LANG]
            if metadata.include_source
            else metadata.languages
        )
        listeners = await room_manager.store.listener_counts(room_id, count_langs)
        return {
            "room_id": room_id,
            "status": metadata.status,
            "languages": metadata.languages,
            "listeners": listeners,
            "include_source": metadata.include_source,
            "source_language": metadata.input_language,
        }

    @router.post("/rooms/{room_id}/join")
    async def join_room(room_id: str) -> dict[str, object]:
        """Issue a short-lived audience token for the captions WebSocket.

        404 for unknown *and* ended rooms — an old QR code behaves exactly
        like a wrong one.
        """
        metadata = await room_manager.store.get(room_id)
        if metadata is None or metadata.status == "ended":
            raise HTTPException(status_code=404, detail="Room not found")
        issued = room_manager.issue_token(room_id)
        return {
            "room_id": room_id,
            "token": issued.token,
            "expires_at": issued.expires_at,
            "languages": metadata.languages,
            "source_language": metadata.input_language,
            "include_source": metadata.include_source,
            "status": metadata.status,
        }

    @router.websocket("/rooms/{room_id}/captions")
    async def captions_ws(websocket: WebSocket, room_id: str) -> None:
        """Audience caption stream: auth → room_status → replay → live.

        Query params: ``lang`` (a room target language, or "source" for the
        untranslated transcript), ``token`` (the join JWT). Close codes:
        4001 bad/expired token or unknown room (client re-joins), 1008
        language not offered, 1000 after a terminal room_status "ended",
        1013 client too slow to keep up (reconnect + replay recovers).
        """
        lang = websocket.query_params.get("lang", "")
        token = websocket.query_params.get("token", "")
        # Accept before any close: a close on an unaccepted socket surfaces as
        # an HTTP 403 handshake failure and the client would never see the
        # close code it switches its re-join behavior on.
        await websocket.accept()
        metadata = await room_manager.store.get(room_id)
        if metadata is None:
            # Funnel into the re-join flow: POST join will 404 and the client
            # lands in its terminal "room gone" state.
            await websocket.close(code=4001, reason="Unknown or expired room")
            return
        try:
            room_manager.verify_token(token, room_id)
        except TokenError as e:
            await websocket.close(code=4001, reason=str(e))
            return
        if lang != SOURCE_LANG and lang not in metadata.languages:
            await websocket.close(code=1008, reason=f"language {lang!r} not offered")
            return

        listeners = await room_manager.listener_counts(room_id)
        now_ms = int(time.time() * 1000)
        await websocket.send_json(
            build_status_message(
                metadata.status, metadata.languages, listeners, sent_at_ms=now_ms
            )
        )
        if metadata.status == "ended":
            await websocket.close()  # 1000 — terminal for the client
            return

        conn_id = await room_manager.register_listener(room_id, lang)
        subscription = await caption_hub.subscribe(
            [build_captions_channel(room_id, lang), build_status_channel(room_id)]
        )
        pump = asyncio.create_task(
            _pump_captions(
                websocket, subscription, room_manager, room_id, lang, conn_id
            )
        )
        try:
            # Replay after subscribing: a live caption may race in during the
            # replay send, but the client upserts by utterance_id so a rare
            # duplicate collapses; a gap can't happen in this order. The window
            # is counted back from the newest caption, not the clock, as the
            # phone prunes its own lines: someone joining while the speaker
            # pauses sees what the phones already in the room still show.
            history = await room_manager.store.read_history(room_id, lang, since_ms=0)
            if history:
                newest_ms = max(e.sent_at_ms for e in history)
                since_ms = newest_ms - settings.caption_replay_secs * 1000
                history = [e for e in history if e.sent_at_ms >= since_ms]
            for entry in history:
                await websocket.send_json(
                    build_caption_message(
                        lang=lang,
                        utterance_id=entry.utterance_id,
                        text=entry.text,
                        final=True,
                        sequence=0,
                        sent_at_ms=entry.sent_at_ms,
                        replay=True,
                    )
                )
            # The audience never sends application data; this read exists to
            # notice disconnects immediately (and tolerates stray messages).
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            await caption_hub.unsubscribe(subscription)
            await room_manager.store.remove_listener(room_id, lang, conn_id)

    return router
