import { useCallback, useEffect, useState } from "react";
import { api, AuthError, getWake, setWake, type SessionInfo, type Stats } from "./api";
import { listeningToApply, readListening, rememberListening } from "./listening";
import { Sidebar, type Theme } from "./components/Sidebar";
import { Caption, greetingCaption } from "./components/WakeChip";
import { BootSequence } from "./hud/BootSequence";
import { storageGet, storageSet, useLook, type Look } from "./hud/useLook";
import { ChatPage } from "./pages/ChatPage";
import { StatsPage } from "./pages/StatsPage";
import { TracesPage } from "./pages/TracesPage";
import { useChat } from "./useChat";

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
  const { setWakeStatus } = chat;
  useEffect(() => {
    getWake()
      .then((status) => {
        setWakeStatus(status); // later changes arrive over the socket
        const want = listeningToApply(readListening(), status); // this browser turned listening off before
        if (want !== null) return setWake(want).then(setWakeStatus);
      })
      .catch(() => undefined);
  }, [setWakeStatus]);
  const toggleWake = useCallback(
    (enabled: boolean) => {
      rememberListening(enabled);
      void setWake(enabled).then(setWakeStatus).catch(() => undefined);
    },
    [setWakeStatus],
  );

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
  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <Sidebar
        look={look}
        setLook={setLook}
        theme={theme}
        setTheme={setTheme}
        page={page}
        session={session}
        connection={chat.state}
        turns={chat.turns.length}
        running={chat.running}
        onNewConversation={() => {
          navigate("#/chat");
          chat.newConversation();
        }}
        wakeStatus={chat.wakeStatus}
        onToggleWake={toggleWake}
        speakAnswers={chat.speakAnswers}
        onSpeakAnswers={chat.setSpeakAnswers}
      />

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
      {look === "classic" && <Caption item={greetingCaption(chat.wake)} />}
      <Caption item={chat.voiceNote} />
    </div>
  );
}
