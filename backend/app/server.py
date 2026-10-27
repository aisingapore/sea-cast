"""The FastAPI app: shared state, the routes, and the frontend.

``create_app`` builds the state the routes share (``AppState``) and includes one
router per group from ``app.routes``:

- ``speaker``: ``/ws`` streams the mic into the session's pipeline, and
  ``/stop-session`` ends the session.
- ``rooms``: ``/rooms`` creates a room for the next session, ``/rooms/{id}`` and
  ``/rooms/{id}/join`` report on it and issue join tokens, and
  ``/rooms/{id}/captions`` streams one language's captions.
- ``setup``: ``/stt-profiles``, ``/translators`` and ``/glossary`` tell the
  dashboard what's configured, plus ``/health``.
- ``admin``: ``/admin/login``, and the recorded sessions under ``/sessions``.

The frontend is served at "/" when ``static_dir`` is set.
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from app.config import Settings, get_settings
from app.delivery.captions import CaptionHub, InProcessCaptionHub
from app.delivery.rooms import MemoryRoomStore, RoomManager
from app.recording.history import SessionStore
from app.routes import admin, rooms, setup, speaker
from app.routes.state import AppState
from app.translation.terminology import preload_glossary


class _SPAStaticFiles(StaticFiles):
    """Serves the built frontend, with ``index.html`` for paths that aren't files.

    Pages like ``/join/AB3D7K`` or ``/history`` are not files, they are routes
    inside the frontend app, which loads ``index.html`` and shows the page for the
    URL. When such a URL is opened directly (a QR scan, a refresh, a bookmark),
    the browser asks the server for it, and plain ``StaticFiles`` would return
    404. So a path with no matching file gets ``index.html`` instead. Real files
    (``index.html``, ``assets/...``) are served as usual. API routes are matched
    before this, so they are unaffected.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve the file at ``path``, or ``index.html`` if there is none.

        Args:
            path: The requested path, relative to the frontend directory.
            scope: The request's ASGI scope.

        Returns:
            The file's response, or ``index.html``'s for a missing file.
        """
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


def create_app(settings: Settings) -> FastAPI:
    """Build the FastAPI application: shared state, routes and the built frontend.

    Args:
        settings: The app settings.

    Returns:
        The application, with every route registered. The frontend is mounted
        at "/" when ``settings.static_dir`` is set.
    """
    # Load the glossary now, so a missing install or a broken file stops the server
    # here rather than in the first speaker's session.
    preload_glossary(settings)
    # Same for the system prompt: a missing file or {language} fails here.
    settings.load_system_prompt()

    # Room state, audience join tokens and caption fan-out all live in this
    # process's memory, so one server process must hold every connection; a
    # restart drops live rooms.
    room_store = MemoryRoomStore(
        room_ttl_secs=settings.room_ttl_secs,
        history_maxlen=settings.caption_history_maxlen,
    )
    # Signing secret shared by audience join tokens and admin-history login
    # tokens (distinct ``aud`` claims; see app/admin_auth.py). A per-process
    # secret is enough: rooms don't outlive the process, and admins just log in
    # again after a restart.
    jwt_secret = secrets.token_hex(32)
    room_manager = RoomManager(
        room_store,
        jwt_secret=jwt_secret,
        jwt_ttl_secs=settings.jwt_ttl_secs,
    )
    caption_hub: CaptionHub = InProcessCaptionHub()
    state = AppState(
        settings=settings,
        room_manager=room_manager,
        caption_hub=caption_hub,
        session_store=SessionStore(settings.recordings_dir),
        jwt_secret=jwt_secret,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await caption_hub.start()
        yield
        await caption_hub.stop()

    api = FastAPI(title="sea-cast", lifespan=lifespan)
    # Exposed for tests.
    api.state.app_state = state

    api.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    for routes in (setup, admin, speaker, rooms):
        api.include_router(routes.build_router(state))

    # Serve the built frontend bundle at "/" (same-origin). Mounted last so
    # every explicit route above takes precedence; unknown paths fall back to
    # index.html (_SPAStaticFiles) so SPA deep links like /join/AB3D7K work.
    # Skipped in local dev where static_dir is unset.
    if settings.static_dir is not None and settings.static_dir.is_dir():
        api.mount(
            "/",
            _SPAStaticFiles(directory=settings.static_dir, html=True),
            name="spa",
        )

    return api


def app() -> FastAPI:
    """Uvicorn --factory entry point: ``uvicorn app.server:app --factory``."""
    return create_app(get_settings())
