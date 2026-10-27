"""Admin routes: /admin/login and the recorded sessions under /sessions."""

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.admin_auth import issue_admin_token, verify_admin_token, verify_credentials
from app.delivery.rooms import TokenError
from app.routes.state import AppState


class _AdminLoginBody(BaseModel):
    username: str
    password: str


def build_router(state: AppState) -> APIRouter:
    """Return this module's routes, bound to the app's shared state.

    Args:
        state: The app's shared state.

    Returns:
        The router, for ``FastAPI.include_router``.
    """
    settings = state.settings
    jwt_secret = state.jwt_secret
    router = APIRouter()

    # The /sessions* endpoints expose every speaker's recordings, so they need an
    # admin token: POST /admin/login trades the shared credential for a
    # short-lived JWT, and ``require_admin`` checks it on each request.
    @router.post("/admin/login")
    async def admin_login(body: _AdminLoginBody) -> dict[str, object]:
        """Exchange the shared admin credential for a short-lived admin token."""
        if not settings.admin_username or not settings.admin_password:
            # Fail closed: an unconfigured deploy grants no access.
            raise HTTPException(status_code=503, detail="admin login is not configured")
        if not verify_credentials(
            body.username,
            body.password,
            expected_username=settings.admin_username,
            expected_password=settings.admin_password,
        ):
            raise HTTPException(status_code=401, detail="invalid credentials")
        issued = issue_admin_token(jwt_secret, ttl_secs=settings.jwt_ttl_secs)
        return {"token": issued.token, "expires_at": issued.expires_at}

    async def require_admin(
        authorization: str | None = Header(default=None),
        token: str | None = None,
    ) -> None:
        """Reject requests without a valid admin token (401).

        Accepts the token from the ``Authorization: Bearer`` header *or* a
        ``?token=`` query param — the query path is required because the
        download link (``<a href>``) and audio (``<audio src>``) are plain GETs
        that can't set headers, mirroring the captions WebSocket.
        """
        raw = token
        if not raw and authorization:
            raw = authorization.removeprefix("Bearer ").strip()
        if not raw:
            raise HTTPException(status_code=401, detail="admin authentication required")
        try:
            verify_admin_token(raw, jwt_secret)
        except TokenError as e:
            raise HTTPException(status_code=401, detail="invalid admin token") from e

    store = state.session_store

    @router.get("/sessions", dependencies=[Depends(require_admin)])
    async def list_sessions() -> list[dict[str, object]]:
        """Summaries of all recorded sessions, newest first."""
        return store.list_sessions()

    # Declared before /sessions/{session_id} so the literal "download" segment
    # isn't captured as a session id.
    @router.get("/sessions/download", dependencies=[Depends(require_admin)])
    async def download_sessions(ids: str = "") -> FileResponse:
        """Zip selected sessions (``ids`` comma-separated) or all when omitted."""
        id_list = [i for i in ids.split(",") if i] or None
        archive_path, included = store.build_archive(id_list)
        if not included:
            os.remove(archive_path)
            raise HTTPException(status_code=404, detail="No matching sessions")
        filename = (
            f"sea-cast-session-{included[0]}.zip"
            if len(included) == 1
            else "sea-cast-sessions.zip"
        )
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(os.remove, archive_path),
        )

    @router.delete("/sessions", dependencies=[Depends(require_admin)])
    async def delete_sessions(ids: str = "") -> dict[str, object]:
        """Delete the sessions named in ``ids`` (comma-separated). ids required.

        Unlike download, an omitted-ids "delete everything" is intentionally not
        allowed — the caller must name the sessions to remove.
        """
        id_list = [i for i in ids.split(",") if i]
        if not id_list:
            raise HTTPException(status_code=400, detail="No session ids provided")
        deleted = store.delete_sessions(id_list)
        if not deleted:
            raise HTTPException(status_code=404, detail="No matching sessions")
        return {"deleted": deleted}

    @router.get("/sessions/{session_id}", dependencies=[Depends(require_admin)])
    async def get_session(session_id: str) -> dict[str, object]:
        """Full metadata + transcript + translation text for one session."""
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @router.get("/sessions/{session_id}/audio", dependencies=[Depends(require_admin)])
    async def get_session_audio(session_id: str) -> FileResponse:
        """Stream a session's WAV (Starlette FileResponse handles Range for seeking)."""
        path = store.audio_path(session_id)
        if path is None:
            raise HTTPException(status_code=404, detail="Audio not found")
        return FileResponse(path, media_type="audio/wav", filename="audio.wav")

    return router
