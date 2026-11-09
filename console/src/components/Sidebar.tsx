// The sidebar (a top bar on narrow screens), the same in both looks. Two controls are always shown:
// "New conversation" right under Chat, and the Speaker switch next to Wake word. Each is disabled, with a
// tooltip saying why, when it can't be used (sidebar.ts); neither is ever hidden.
import { AppWindow, ChartColumn, ChartGantt, FlaskConical, MessagesSquare, Monitor, Moon, Radar, SquarePen, Sun } from "lucide-react";
import { Fragment } from "react";
import type { SessionInfo, WakeStatus } from "../api";
import { FalconMark } from "../hud/FalconMark";
import type { Look } from "../hud/useLook";
import { newConversationState, speakerState } from "../sidebar";
import type { ConnectionState } from "../useChat";
import { cx } from "./ui";
import { SpeakSwitch, WakeChip } from "./WakeChip";

export type Theme = "light" | "dark" | "system";

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

export interface SidebarProps {
  look: Look;
  setLook: (l: Look) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
  page: string;
  session: SessionInfo | null;
  connection: ConnectionState;
  turns: number;
  running: boolean;
  onNewConversation: () => void;
  wakeStatus: WakeStatus;
  onToggleWake: (enabled: boolean) => void;
  speakAnswers: boolean;
  onSpeakAnswers: (on: boolean) => void;
}

export function Sidebar(p: SidebarProps) {
  const models = p.session?.models ?? [];
  const fresh = newConversationState(p.turns, p.running, p.connection === "open");
  const speaker = speakerState(p.wakeStatus, !!p.session?.demo);
  return (
    <nav className="flex shrink-0 items-center gap-1 border-b border-stone-200/70 bg-white/60 px-3 py-2 backdrop-blur dark:border-stone-800/70 dark:bg-stone-900/40 md:w-56 md:flex-col md:items-stretch md:gap-0 md:border-b-0 md:border-r md:px-3 md:py-4">
      <div className="mr-2 md:mb-6 md:mr-0 md:px-2">{p.look === "hud" ? <HudLogo /> : <Logo />}</div>
      <div className="flex gap-1 md:flex-col">
        {NAV.map(({ id, label, icon: Icon }) => (
          <Fragment key={id}>
            <a
              href={`#/${id}`}
              aria-current={p.page === id ? "page" : undefined}
              className={cx(
                "flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium transition-colors",
                p.page === id ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900" : "text-stone-600 hover:bg-stone-200/60 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100",
              )}
            >
              <Icon className="size-4" />
              <span className="hidden sm:inline">{label}</span>
            </a>
            {id === "chat" && (
              <button
                type="button"
                onClick={p.onNewConversation}
                disabled={fresh.disabled}
                title={fresh.title}
                aria-label={`New conversation${fresh.disabled ? `: ${fresh.title}` : ""}`}
                className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm font-medium text-stone-600 transition-colors hover:bg-stone-200/60 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-stone-600 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100 md:pl-8"
              >
                <SquarePen className="size-4" />
                <span className="hidden sm:inline">New conversation</span>
              </button>
            )}
          </Fragment>
        ))}
      </div>
      <div className="ml-auto flex items-center gap-3 md:mt-auto md:ml-0 md:flex-col md:items-stretch md:gap-3 md:px-2">
        {p.session?.demo && (
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
          <span className={cx("size-1.5 rounded-full", p.connection === "open" ? "bg-emerald-500" : p.connection === "connecting" ? "bg-amber-400" : "bg-red-500")} />
          {p.connection === "open" ? "Connected" : p.connection === "connecting" ? "Connecting" : "Disconnected"}
        </span>
        <div className="flex items-center gap-2 md:flex-col md:items-stretch">
          <WakeChip status={p.wakeStatus} onToggle={p.onToggleWake} />
          <SpeakSwitch on={p.speakAnswers} onChange={p.onSpeakAnswers} disabled={speaker.disabled} title={speaker.title} />
          <LookSwitch look={p.look} setLook={p.setLook} />
          {p.look === "classic" && <ThemeSwitch theme={p.theme} setTheme={p.setTheme} />}
        </div>
      </div>
    </nav>
  );
}
