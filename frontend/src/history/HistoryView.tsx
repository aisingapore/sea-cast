// /history — the admin-only session archive.
// Gates HistoryPanel behind a username/password login: the shared credential is
// traded for a short-lived token (useAdminToken), which the panel attaches to
// every /sessions* call. To use another login method, swap useAdminToken;
// this component and HistoryPanel stay as they are.

import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { ApiError } from "../lib/api";
import { useAdminToken } from "../lib/useAdminToken";
import { useTheme } from "../lib/useTheme";
import { HistoryPanel } from "./HistoryPanel";
// The styles this page shares with the dashboard live in App.css. Opened
// directly, /history never loads App.tsx, so import them here.
import "../App.css";

export default function HistoryView() {
  const { theme, toggle } = useTheme();
  const { login, logout, getToken } = useAdminToken();
  // Read the token fresh each render so an expiry that lapsed while the page
  // was open falls back to the login form on the next render.
  const token = getToken();

  return (
    <div className="app">
      <header className="app-header">
        <h1>SEA-CAST</h1>
        <span className="app-subtitle">session history</span>
        <Link to="/" className="header-link">
          ← Dashboard
        </Link>
        {token && (
          <button type="button" className="header-link" onClick={logout}>
            Log out
          </button>
        )}
        <button className="theme-toggle" onClick={toggle} aria-label="Toggle theme">
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>
      {token ? (
        <HistoryPanel token={token} onAuthError={logout} />
      ) : (
        <LoginForm onSubmit={login} />
      )}
    </div>
  );
}

function LoginForm({
  onSubmit,
}: {
  onSubmit: (username: string, password: string) => Promise<void>;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    onSubmit(username, password)
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Incorrect username or password.");
        } else if (err instanceof ApiError && err.status === 503) {
          setError("Admin access is not configured on the server.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setBusy(false));
  };

  return (
    <form className="admin-login" onSubmit={submit}>
      <h2>Admin sign-in</h2>
      <p className="admin-login-hint">
        Session recordings are restricted to admin personnel.
      </p>
      <label>
        Username
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
        />
      </label>
      <label>
        Password
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />
      </label>
      {error && <p className="admin-login-error">{error}</p>}
      <button
        className="btn btn-start"
        type="submit"
        disabled={busy || !username || !password}
      >
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
