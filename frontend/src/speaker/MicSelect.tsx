import { useState } from "react";
import { usePipecatClientMediaDevices } from "@pipecat-ai/client-react";

export function MicSelect() {
  const { availableMics, selectedMic, updateMic } = usePipecatClientMediaDevices();
  // The hook's `selectedMic` stays empty until the mic is live, so the dropdown
  // would keep showing the first mic after the user picks another. Remember the
  // pick here instead.
  const [selectedId, setSelectedId] = useState("");

  if (availableMics.length === 0) return null;

  const hookId = (selectedMic as MediaDeviceInfo | null | undefined)?.deviceId ?? "";
  // Fall back to the first device — the one the transport actually uses by default —
  // so the displayed option always matches what's in effect.
  const value = selectedId || hookId || availableMics[0]?.deviceId || "";

  return (
    <label className="control-group">
      <span>Microphone</span>
      <select
        value={value}
        onChange={(e) => {
          setSelectedId(e.target.value);
          updateMic(e.target.value);
        }}
      >
        {availableMics.map((mic) => (
          <option key={mic.deviceId} value={mic.deviceId}>
            {mic.label || `Microphone ${mic.deviceId.slice(0, 6)}`}
          </option>
        ))}
      </select>
    </label>
  );
}
