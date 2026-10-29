import { useCallback, useEffect, useState } from "react";
import { AppWindow, ChartColumn, ChartGantt, FlaskConical, MessagesSquare, Monitor, Moon, Radar, Sun } from "lucide-react";
import { api, AuthError, type SessionInfo, type Stats } from "./api";
import { cx } from "./components/ui";
import { BootSequence } from "./hud/BootSequence";
import { FalconMark } from "./hud/FalconMark";
import { storageGet, storageSet, useLook, type Look } from "./hud/useLook";
import { ChatPage } from "./pages/ChatPage";
import { StatsPage } from "./pages/StatsPage";
import { TracesPage } from "./pages/TracesPage";
import { useChat } from "./useChat";

type Theme = "light" | "dark" | "system";

// The HUD look is dark only; the classic look follows this light / system / dark choice.
function useTheme(look: Look): [Theme, (t: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => (storageGet("kestrel-theme") as Theme) || "system");
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () =>
      document.documentElement.classList.toggle("dark", look === "hud" || theme === "dark" || (theme === "system" && media.matches));
    apply();
    storageSet("kestrel-theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme, look]);
  return [theme, setTheme];
}

const readHash = () => (window.location.hash.replace(/^#\/?/, "") || "chat").split("/");

function useHashRoute(): [string[], (hash: string) => void] {
  const [route, setRoute] = useState(readHash);
  useEffect(() => {
    const onChange = () => setRoute(readHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [route, (hash) => (window.location.hash = hash)];
}

const NAV = [
  { id: "chat", label: "Chat", icon: MessagesSquare },
  { id: "traces", label: "Traces", icon: ChartGantt },
  { id: "stats", label: "Stats", icon: ChartColumn },
];

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <img src="/kestrel.svg" alt="" className="size-8 rounded-lg shadow-sm" />
      <div className="hidden leading-tight sm:block">
        <div className="font-semibold tracking-tight">Kestrel</div>
        <div className="text-[11px] text-stone-500 dark:text-stone-400">personal agent</div>
      </div>
    </div>
  );
}

function HudLogo() {
  return (
    <div className="flex items-center gap-2.5 text-accent-300">
      <FalconMark className="size-8 drop-shadow-[0_0_6px_rgb(34_211_238/0.6)]" />
      <div className="hidden leading-tight sm:block">
        <div className="hud-wordmark text-sm">KESTREL</div>
        <div className="hud-font text-[9px] uppercase text-stone-400">personal agent</div>
      </div>
    </div>
  );
}

function LookSwitch({ look, setLook }: { look: Look; setLook: (l: Look) => void }) {
  const options: [Look, string][] = [["classic", "Classic"], ["hud", "HUD"]];
  return (
    <div className="inline-flex rounded-lg bg-stone-200/60 p-0.5 text-[11px] font-semibold dark:bg-stone-800/80" role="radiogroup" aria-label="Look">
      {options.map(([value, label]) => (
        <button
          key={value}
          role="radio"
          aria-checked={look === value}
          aria-label={label}
          onClick={() => setLook(value)}
          className={cx(
            "flex items-center gap-1 rounded-md px-2 py-1",
            look === value ? "bg-white text-stone-900 shadow-sm dark:bg-stone-700 dark:text-white" : "text-stone-500 hover:text-stone-800 dark:text-stone-400 dark:hover:text-stone-200",
          )}
        >
          {value === "hud" ? <Radar className="size-3.5" /> : <AppWindow className="size-3.5 sm:hidden" />}
          <span className="hidden sm:inline">{label}</span>
        </button>
      ))}
    </div>
  );
}

function ThemeSwitch({ theme, setTheme }: { theme: Theme; setTheme: (t: Theme) => void }) {
  const options: [Theme, typeof Sun][] = [["light", Sun], ["system", Monitor], ["dark", Moon]];
  return (
    <div className="inline-flex rounded-lg bg-stone-200/60 p-0.5 dark:bg-stone-800/80" role="radiogroup" aria-label="Theme">
      {options.map(([value, Icon]) => (
        <button
          key={value}
          role="radio"
          aria-checked={theme === value}
          aria-label={value}
          onClick={() => setTheme(value)}
          className={cx("rounded-md p-1.5", theme === value ? "bg-white text-stone-900 shadow-sm dark:bg-stone-700 dark:text-white" : "text-stone-500 hover:text-stone-800 dark:hover:text-stone-200")}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}

export function App() {
  const [route, navigate] = useHashRoute();
  const [look, setLook] = useLook();
  const [theme, setTheme] = useTheme(look);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [authError, setAuthError] = useState(false);

  const refreshStats = useCallback(() => {
    api<Stats>("/api/stats").then(setStats).catch((e) => e instanceof AuthError && setAuthError(true));
  }, []);
  const chat = useChat(refreshStats);

  useEffect(() => {
    api<SessionInfo>("/api/session").then(setSession).catch((e) => e instanceof AuthError && setAuthError(true));
    refreshStats();
  }, [refreshStats]);
  useEffect(() => {
    if (route[0] === "stats") refreshStats();
  }, [route, refreshStats]);

  if (authError) {
    return (
      <div className="mx-auto max-w-md px-6 pt-[20vh]">
        <Logo />
        <p className="mt-6 text-stone-600 dark:text-stone-300">
          Open the link printed in the terminal where you ran <code className="rounded bg-stone-200 px-1 dark:bg-stone-800">uv run kestrel web</code>. It contains your access token.
        </p>
      </div>
    );
  }

  const page = route[0];
  const models = session?.models ?? [];
  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <nav className="flex shrink-0 items-center gap-1 border-b border-stone-200/70 bg-white/60 px-3 py-2 backdrop-blur dark:border-stone-800/70 dark:bg-stone-900/40 md:w-56 md:flex-col md:items-stretch md:gap-0 md:border-b-0 md:border-r md:px-3 md:py-4">
        <div className="mr-2 md:mb-6 md:mr-0 md:px-2">{look === "hud" ? <HudLogo /> : <Logo />}</div>
        <div className="flex gap-1 md:flex-col">
          {NAV.map(({ id, label, icon: Icon }) => (
            <a
              key={id}
              href={`#/${id}`}
              aria-current={page === id ? "page" : undefined}
              className={cx(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                page === id ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "text-stone-600 hover:bg-stone-200/60 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100",
              )}
            >
              <Icon className="size-4" />
              <span className="hidden sm:inline">{label}</span>
            </a>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-3 md:mt-auto md:ml-0 md:flex-col md:items-stretch md:gap-3 md:px-2">
          {session?.demo && (
            <span className="hidden items-center gap-1.5 rounded-md bg-violet-100 sm:inline-flex px-2 py-1 text-[11px] font-semibold text-violet-800 dark:bg-violet-950/60 dark:text-violet-200">
              <FlaskConical className="size-3.5" /> Demo mode
            </span>
          )}
          {models.length > 0 && (
            <div className="hidden text-[11px] leading-5 text-stone-500 md:block">
              <div className="font-medium uppercase tracking-wider">Models</div>
              {models.map((m, i) => (
                <div key={m.provider} className="truncate" title={`${m.provider} / ${m.model}`}>
                  {i > 0 && <span className="text-stone-400">then </span>}
                  <span className="text-stone-700 dark:text-stone-300">{m.provider}</span> <span className="text-stone-400">{m.model}</span>
                </div>
              ))}
            </div>
          )}
          <span className="hidden items-center gap-1.5 text-[11px] text-stone-500 md:flex">
            <span className={cx("size-1.5 rounded-full", chat.state === "open" ? "bg-emerald-500" : chat.state === "connecting" ? "bg-amber-400" : "bg-red-500")} />
            {chat.state === "open" ? "Connected" : chat.state === "connecting" ? "Connecting" : "Disconnected"}
          </span>
          <div className="flex items-center gap-2 md:flex-col md:items-stretch">
            <LookSwitch look={look} setLook={setLook} />
            {look === "classic" && <ThemeSwitch theme={theme} setTheme={setTheme} />}
          </div>
        </div>
      </nav>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto">
        {page === "traces" ? (
          <TracesPage traceId={route[1]} navigate={navigate} />
        ) : page === "stats" ? (
          <StatsPage stats={stats} />
        ) : (
          <ChatPage chat={chat} session={session} stats={stats} onRated={refreshStats} look={look} />
        )}
      </main>
      {look === "hud" && <BootSequence session={session} connection={chat.state} />}
    </div>
  );
}
