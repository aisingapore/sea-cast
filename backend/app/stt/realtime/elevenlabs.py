"""ElevenLabs realtime STT with latency metrics consistent with the batch adapter.

A thin extension of Pipecat's ``ElevenLabsRealtimeSTTService``, which streams the
audio over one WebSocket and lets ElevenLabs' server-side VAD commit each transcript.
Only two of the metrics Pipecat reports change:

- **TTFB.** The base ``STTService`` reports it as soon as a *finalized*
  ``TranscriptionFrame`` arrives; otherwise only after a 2s timeout from the end of
  speech, using the last transcript's arrival time. Pipecat marks ElevenLabs'
  VAD-committed transcripts as not final (``finalized`` is set only for
  ``CommitStrategy.MANUAL``), so every utterance would take the timeout: the metric
  arrives late, and is missing if the commit comes after the 2s window. ``push_frame``
  marks every transcript final, as the batch adapter's ``SegmentedSTTService`` does.
- **Processing time** is not reported. For a streaming STT it would time speech
  onset → commit (utterance length plus endpoint delay), not real processing.
"""

from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService


class ElevenLabsRealtimeSTT(ElevenLabsRealtimeSTTService):
    """``ElevenLabsRealtimeSTTService`` with TTFB and processing metrics fixed."""

    async def _start_metrics(self) -> None:
        """Skip ``start_processing_metrics``, so no processing-time metric is emitted.

        The matching stop on commit is then a guarded no-op in the base class.
        """
        return

    async def push_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        """Mark each transcript final before pushing it, so TTFB is reported at once.

        Args:
            frame: The frame to push.
            direction: The direction of frame flow in the pipeline.
        """
        if isinstance(frame, TranscriptionFrame):
            frame.finalized = True
        await super().push_frame(frame, direction)
