"""The state the app's routes share."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.config import Settings
from app.delivery.captions import CaptionHub
from app.delivery.rooms import RoomManager
from app.recording.history import SessionStore


@dataclass(frozen=True)
class AppState:
    """What the routes share: settings, rooms, captions, recordings and sessions.

    One per app, built by ``create_app``.

    Attributes:
        settings: The app settings.
        room_manager: Rooms and audience tokens.
        caption_hub: Delivers captions to audience sockets.
        session_store: Reads recorded sessions for the history page.
        jwt_secret: Signs audience and admin tokens.
        stop_fns: Each live speaker session's stop function, by session id.
            /stop-session calls it to let in-flight translations finish.
    """

    settings: Settings
    room_manager: RoomManager
    caption_hub: CaptionHub
    session_store: SessionStore
    jwt_secret: str
    stop_fns: dict[str, Callable[[], Awaitable[None]]] = field(default_factory=dict)
