/* eslint-disable react-refresh/only-export-components --
   entry point, not a hot-refresh boundary; the lazy() consts trip the rule */
import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import "./index.css";

// Every route is lazy so each surface downloads only its own chunk: audience
// phones hitting /join/… never pull the dashboard's Pipecat + chart.js bundle,
// and the dashboard never pulls the audience CSS. (The backend serves
// index.html for unknown paths — the SPA fallback — so deep links work.)
const App = lazy(() => import("./App.tsx"));
const AudienceView = lazy(() => import("./audience/AudienceView.tsx"));
const MonitorView = lazy(() => import("./monitor/MonitorView.tsx"));
const HistoryView = lazy(() => import("./history/HistoryView.tsx"));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={null}>
        <Routes>
          <Route path="/" element={<App />} />
          <Route path="/join/:roomCode" element={<AudienceView />} />
          <Route path="/monitor/:roomCode" element={<MonitorView />} />
          <Route path="/history" element={<HistoryView />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
);
