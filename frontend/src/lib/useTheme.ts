// Light/dark theme, persisted in localStorage and applied to <html data-theme>.
// Shared by the dashboard (App) and the History page so both surfaces honour
// the same stored preference and can toggle it.

import { useCallback, useEffect, useState } from "react";

export function useTheme(): { theme: "light" | "dark"; toggle: () => void } {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const stored = localStorage.getItem("sea-cast-theme");
    if (stored === "light" || stored === "dark") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("sea-cast-theme", theme);
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );
  return { theme, toggle };
}
