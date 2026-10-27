import { InfoTip } from "./InfoTip";
import type { NoiseFilter, SessionSetup } from "./useSessionSetup";

export function SettingsTab({
  setup,
  disabled,
}: {
  setup: SessionSetup;
  disabled: boolean;
}) {
  const { sttProfile, dials, setDials } = setup;
  const batch = sttProfile !== undefined && !sttProfile.streaming;

  return (
    <div className="vad-settings">
      <h2>Audio Input</h2>
      <label className="control-group">
        <span className="control-head">
          Noise filter
          <InfoTip label="Removes background noise. Try RNNoise in a noisy venue. In a quiet environment, it might slightly muffle speech." />
        </span>
        <select
          disabled={disabled}
          value={setup.noiseFilter}
          onChange={(e) => setup.setNoiseFilter(e.target.value as NoiseFilter)}
        >
          <option value="off">Off</option>
          <option value="rnnoise">RNNoise</option>
        </select>
      </label>
      <h2>Voice Activity Detection (VAD) Tuning</h2>
      <label className="control-group">
        <span className="control-head">
          Endpoint silence — {dials.silenceSecs.toFixed(2)}s
          <InfoTip label="How long the speaker must pause before a sentence counts as finished and is sent for transcription and translation. Shorter gives faster captions but can split sentences, whereas longer keeps sentences whole but captions arrive later." />
        </span>
        <input
          type="range"
          min="0.3"
          max="3.0"
          step="0.1"
          disabled={disabled}
          value={dials.silenceSecs}
          onChange={(e) =>
            setDials((d) => ({ ...d, silenceSecs: parseFloat(e.target.value) }))
          }
        />
      </label>
      {batch && (
        <label className="control-group">
          <span className="control-head">
            VAD confidence — {dials.confidence.toFixed(2)}
            <InfoTip label="How sure the voice detector must be that a sound is speech before it starts listening. Raise it if coughs, applause or background chatter get transcribed; lower it if quiet speech is missed." />
          </span>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            disabled={disabled}
            value={dials.confidence}
            onChange={(e) =>
              setDials((d) => ({ ...d, confidence: parseFloat(e.target.value) }))
            }
          />
        </label>
      )}
      {batch && (
        <label className="control-group">
          <span className="control-head">
            Max segment —{" "}
            {dials.maxSegmentSecs > 0 ? `${dials.maxSegmentSecs.toFixed(0)}s` : "off"}
            <InfoTip label="If the speaker talks this long without a pause, what they've said so far is sent for transcription anyway, so captions don't stall. Lower values bring captions sooner but can cut sentences. Set to 0 to turn it off: the app then waits for a pause no matter how long it takes." />
          </span>
          <input
            type="range"
            min="0"
            max="30"
            step="1"
            disabled={disabled}
            value={dials.maxSegmentSecs}
            onChange={(e) =>
              setDials((d) => ({ ...d, maxSegmentSecs: parseFloat(e.target.value) }))
            }
          />
        </label>
      )}
      {sttProfile?.streaming && (
        <label className="control-group">
          <span className="control-head">
            VAD sensitivity — {dials.threshold.toFixed(2)}
            <InfoTip label="How readily the transcription service treats a sound as speech. Lower values pick up quieter speech but also more background noise. Raise it in a noisy room." />
          </span>
          <input
            type="range"
            min="0.1"
            max="0.9"
            step="0.05"
            disabled={disabled}
            value={dials.threshold}
            onChange={(e) =>
              setDials((d) => ({ ...d, threshold: parseFloat(e.target.value) }))
            }
          />
        </label>
      )}
    </div>
  );
}
