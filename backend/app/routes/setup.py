"""Setup routes: what the dashboard can choose from, and /health."""

from fastapi import APIRouter

from app.config import ElevenLabsSttProfile
from app.languages import APP_SOURCE, APP_TARGET
from app.routes.state import AppState
from app.translation.terminology import load_glossary


def build_router(state: AppState) -> APIRouter:
    """Return this module's routes, bound to the app's shared state.

    Args:
        state: The app's shared state.

    Returns:
        The router, for ``FastAPI.include_router``.
    """
    settings = state.settings
    router = APIRouter()

    def _in_app_order(codes: frozenset[str], app: tuple[str, ...]) -> list[str]:
        return [c for c in app if c in codes]

    @router.get("/health")
    async def health() -> dict[str, str]:
        """Liveness check."""
        return {"status": "ok"}

    @router.get("/translators")
    async def translators() -> list[dict[str, object]]:
        """Selectable translator profiles for the dashboard.

        The first is the default.
        """
        return [
            {
                "id": p.id,
                "label": p.label,
                "translates_to": _in_app_order(p.translates_to, APP_TARGET),
            }
            for p in settings.translator_profiles
        ]

    @router.get("/glossary")
    async def glossary_info() -> dict[str, object]:
        """Whether a glossary is configured, and the languages it covers.

        The dashboard shows its glossary toggle only when one is available. No path.
        """
        if settings.glossary is None:
            return {"available": False}
        glossary = load_glossary(str(settings.glossary.path))
        return {"available": True, "languages": glossary.languages()}

    @router.get("/stt-profiles")
    async def stt_profiles() -> list[dict[str, object]]:
        """Selectable STT profiles for the dashboard. The first is the default.

        Each has its resolved languages and VAD dial defaults, Silero's stop_secs
        and confidence and the max segment length for the batch adapter,
        ElevenLabs' commit silence and threshold for streaming. No URLs or key
        names.
        """
        result: list[dict[str, object]] = []
        for p in settings.stt_profiles:
            languages = p.resolved_languages
            vad_defaults: dict[str, float] = (
                {
                    "silence_secs": p.options.vad_silence_secs,
                    "threshold": p.options.vad_threshold,
                }
                if isinstance(p, ElevenLabsSttProfile)
                else {
                    "silence_secs": settings.vad_stop_secs,
                    "confidence": settings.vad_confidence,
                    "max_segment_secs": settings.stt_max_segment_secs,
                }
            )
            result.append(
                {
                    "id": p.id,
                    "label": p.label,
                    "adapter": p.adapter,
                    "streaming": p.streaming,
                    "languages": {
                        "recognizes": _in_app_order(
                            languages["recognizes"], APP_SOURCE
                        ),
                        "supports_auto": languages["supports_auto"],
                        "auto_only": languages["auto_only"],
                    },
                    "vad_defaults": vad_defaults,
                }
            )
        return result

    return router
