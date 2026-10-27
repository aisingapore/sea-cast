import { useEffect, useRef } from "react";
import { usePipecatClientMediaTrack } from "@pipecat-ai/client-react";

export function MicWaveform() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const track = usePipecatClientMediaTrack("audio", "local");

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const barW = 3;
    const gap = 2;
    const count = Math.floor(canvas.width / (barW + gap));

    function getAccent() {
      return (
        getComputedStyle(document.documentElement)
          .getPropertyValue("--accent")
          .trim() || "#2563eb"
      );
    }

    if (!track) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = getAccent();
      const mid = Math.floor(canvas.height / 2);
      for (let i = 0; i < count; i++) {
        ctx.fillRect(i * (barW + gap), mid - 1, barW, 2);
      }
      return;
    }

    const audioCtx = new AudioContext();
    const source = audioCtx.createMediaStreamSource(new MediaStream([track]));
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    const data = new Uint8Array(analyser.frequencyBinCount);
    let rafId = 0;

    // canvas and ctx are guaranteed non-null by the early returns above;
    // TypeScript doesn't propagate that narrowing into nested function bodies.
    function draw() {
      rafId = requestAnimationFrame(draw);
      analyser.getByteFrequencyData(data);
      ctx!.clearRect(0, 0, canvas!.width, canvas!.height);
      ctx!.fillStyle = getAccent();
      const barCount = Math.min(data.length, count);
      for (let i = 0; i < barCount; i++) {
        const v = (data[i] ?? 0) / 255;
        const h = Math.max(2, v * canvas!.height);
        ctx!.fillRect(i * (barW + gap), (canvas!.height - h) / 2, barW, h);
      }
    }
    draw();

    return () => {
      cancelAnimationFrame(rafId);
      void audioCtx.close();
    };
  }, [track]);

  return (
    <div className="mic-waveform">
      <canvas ref={canvasRef} width={80} height={28} />
    </div>
  );
}
