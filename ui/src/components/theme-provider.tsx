/**
 * Theme: the standard shadcn CSS-variable approach (a `.dark` class on <html>),
 * defaulting to dark because this is presented on a projector in a meeting room.
 * The choice persists to localStorage so a reload does not flip mid-demo.
 */

import * as React from "react"
import { Monitor, Moon, Sun } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

type Theme = "dark" | "light" | "system"

const STORAGE_KEY = "agentcore-demo-theme"

interface ThemeContextValue {
  theme: Theme
  setTheme: (theme: Theme) => void
  resolved: "dark" | "light"
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null)

function readStored(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === "dark" || stored === "light" || stored === "system") return stored
  } catch {
    // localStorage can throw in a hardened browser profile; dark is the default.
  }
  return "dark"
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<Theme>(readStored)
  const [systemDark, setSystemDark] = React.useState(
    () => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? true,
  )

  React.useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)")
    if (!query) return
    const handler = (event: MediaQueryListEvent) => setSystemDark(event.matches)
    query.addEventListener("change", handler)
    return () => query.removeEventListener("change", handler)
  }, [])

  const resolved: "dark" | "light" = theme === "system" ? (systemDark ? "dark" : "light") : theme

  React.useEffect(() => {
    const root = document.documentElement
    root.classList.toggle("dark", resolved === "dark")
    root.style.colorScheme = resolved
    // Mirrored onto a data attribute so chart CSS can key off the same source of
    // truth as Tailwind's dark variant.
    root.dataset.theme = resolved
  }, [resolved])

  const setTheme = React.useCallback((next: Theme) => {
    setThemeState(next)
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Non-fatal: the theme still applies for this session.
    }
  }, [])

  const value = React.useMemo(() => ({ theme, setTheme, resolved }), [theme, setTheme, resolved])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeContextValue {
  const context = React.useContext(ThemeContext)
  if (!context) throw new Error("useTheme must be used inside ThemeProvider")
  return context
}

const ORDER: Theme[] = ["dark", "light", "system"]
const LABEL: Record<Theme, string> = {
  dark: "Dark theme",
  light: "Light theme",
  system: "Follow system theme",
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          aria-label={LABEL[theme]}
          onClick={() => setTheme(ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length])}
        >
          <Icon aria-hidden />
        </Button>
      </TooltipTrigger>
      <TooltipContent>{LABEL[theme]}</TooltipContent>
    </Tooltip>
  )
}
