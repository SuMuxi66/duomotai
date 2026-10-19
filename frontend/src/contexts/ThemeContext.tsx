import { createContext, useContext, useMemo, useCallback, useEffect } from "react";
import { getTheme, ThemeMode, Theme } from "../lib/theme";
import { usePersistedState } from "../hooks/usePersistedState";
import { STORAGE_KEYS } from "../lib/storage";

interface ThemeContextValue {
  themeMode: ThemeMode;
  theme: Theme;
  toggleThemeMode: () => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [themeMode, setThemeMode] = usePersistedState<ThemeMode>(STORAGE_KEYS.themeMode, "light");
  const theme: Theme = useMemo(() => getTheme(themeMode), [themeMode]);

  const toggleThemeMode = useCallback(() => {
    setThemeMode((prev: ThemeMode) => (prev === "light" ? "dark" : "light"));
  }, [setThemeMode]);

  useEffect(() => {
    const { body, documentElement } = document;
    body.style.background = theme.bg;
    body.style.color = theme.text;
    documentElement.style.background = theme.bg;
    documentElement.style.color = theme.text;

    return () => {
      body.style.background = "";
      body.style.color = "";
      documentElement.style.background = "";
      documentElement.style.color = "";
    };
  }, [theme]);

  return (
    <ThemeContext.Provider value={{ themeMode, theme, toggleThemeMode }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
