import { useMemo } from "react";
import { PipecatClient } from "@pipecat-ai/client-js";
import { PipecatClientAudio, PipecatClientProvider } from "@pipecat-ai/client-react";
import { WebSocketTransport } from "@pipecat-ai/websocket-transport";
import { Link } from "react-router";
import { useTheme } from "./lib/useTheme";
import { SessionView } from "./speaker/SessionView";
import "./App.css";

// The speaker dashboard (/). The session itself lives in speaker/SessionView.
export default function App() {
  const { theme, toggle } = useTheme();

  // One client for the page's lifetime, reused across sessions.
  const client = useMemo(
    () => new PipecatClient({ transport: new WebSocketTransport(), enableMic: true }),
    [],
  );

  return (
    <PipecatClientProvider client={client}>
      <PipecatClientAudio />
      <div className="app">
        <header className="app-header">
          <h1>SEA-CAST</h1>
          <span className="app-subtitle">live translate</span>
          <Link to="/history" className="header-link">
            History ↗
          </Link>
          <button className="theme-toggle" onClick={toggle} aria-label="Toggle theme">
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </header>
        <SessionView />
      </div>
    </PipecatClientProvider>
  );
}
