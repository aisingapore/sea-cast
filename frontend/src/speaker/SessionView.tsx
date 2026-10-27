import { useState } from "react";
import { MetricsPanel } from "./MetricsPanel";
import { SessionControls } from "./SessionControls";
import { SessionTab } from "./SessionTab";
import { SettingsTab } from "./SettingsTab";
import { useSessionConnection } from "./useSessionConnection";
import { useSessionFeed } from "./useSessionFeed";
import { useSessionSetup } from "./useSessionSetup";

type Tab = "session" | "metrics" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "session", label: "Session" },
  { id: "metrics", label: "Metrics" },
  { id: "settings", label: "Settings" },
];

// The speaker dashboard below the header: the controls bar, an STT error banner
// and the Session / Metrics / Settings tabs.
export function SessionView() {
  const setup = useSessionSetup();
  const feed = useSessionFeed();
  const connection = useSessionConnection();
  const [activeTab, setActiveTab] = useState<Tab>("session");
  // Once a session is live, collapse the (now read-only) controls into a slim
  // bar so that the captions take centre-stage.
  const [settingsOpen, setSettingsOpen] = useState(false);

  const live = connection.isActive || connection.isBusy;

  const handleStart = () => {
    feed.reset();
    setSettingsOpen(false);
    void connection.start(setup);
  };

  return (
    <div className="session">
      <SessionControls
        setup={setup}
        connection={connection}
        onStart={handleStart}
        settingsOpen={settingsOpen}
        onToggleSettings={() => setSettingsOpen((o) => !o)}
      />

      {feed.sttError && (
        <div className="stt-error-banner" role="alert">
          <span>⚠ {feed.sttError.message}</span>
          <button
            className="stt-error-dismiss"
            onClick={feed.dismissSttError}
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}

      <div className="tabs" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={`tab ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="tab-panel">
        {activeTab === "session" && (
          <SessionTab
            room={connection.room}
            live={live}
            transcripts={feed.transcripts}
            translations={feed.translations}
            primaryLanguage={setup.targetLanguages[0]}
          />
        )}
        {activeTab === "metrics" && <MetricsPanel metrics={feed.metrics} />}
        {activeTab === "settings" && <SettingsTab setup={setup} disabled={live} />}
      </div>
    </div>
  );
}
