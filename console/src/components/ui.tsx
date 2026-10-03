import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const variants: Record<Variant, string> = {
  primary:
    "bg-accent-600 text-white shadow-sm hover:bg-accent-700 active:bg-accent-800 disabled:bg-accent-600/50",
  secondary:
    "bg-white text-stone-800 ring-1 ring-stone-300 hover:bg-stone-50 dark:bg-stone-900 dark:text-stone-100 dark:ring-stone-700 dark:hover:bg-stone-800",
  ghost: "text-stone-600 hover:bg-stone-200/60 hover:text-stone-900 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100",
  danger:
    "bg-white text-red-700 ring-1 ring-red-200 hover:bg-red-50 dark:bg-stone-900 dark:text-red-400 dark:ring-red-900/60 dark:hover:bg-red-950/40",
};

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }) {
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        size === "sm" ? "h-8 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        variants[variant],
        className,
      )}
      {...props}
    />
  );
}

const tones = {
  neutral: "bg-stone-100 text-stone-700 ring-stone-200 dark:bg-stone-800/80 dark:text-stone-300 dark:ring-stone-700",
  accent: "bg-accent-50 text-accent-800 ring-accent-200 dark:bg-accent-950/50 dark:text-accent-200 dark:ring-accent-900",
  green: "bg-emerald-50 text-emerald-800 ring-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-900",
  red: "bg-red-50 text-red-800 ring-red-200 dark:bg-red-950/40 dark:text-red-300 dark:ring-red-900",
  amber: "bg-amber-50 text-amber-800 ring-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-900",
  violet: "bg-violet-50 text-violet-800 ring-violet-200 dark:bg-violet-950/40 dark:text-violet-300 dark:ring-violet-900",
};
export type Tone = keyof typeof tones;

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export const riskTone = (risk: string | null | undefined): Tone =>
  risk === "confirm" ? "amber" : risk === "forbidden" ? "red" : "neutral";

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircle className={cx("size-3.5 animate-spin", className)} aria-label="working" />;
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        "rounded-xl bg-white ring-1 ring-stone-200/80 shadow-[0_1px_2px_rgba(28,25,23,0.04)] dark:bg-stone-900 dark:ring-stone-800",
        className,
      )}
    >
      {children}
    </div>
  );
}

export function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <Card className="px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-wider text-stone-500 dark:text-stone-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-stone-500 dark:text-stone-400">{hint}</div>}
    </Card>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <div className="mb-3 rounded-2xl bg-stone-100 p-3 text-stone-500 dark:bg-stone-900 dark:text-stone-400">{icon}</div>
      <div className="font-medium">{title}</div>
      {children && <div className="mt-1 max-w-sm text-sm text-stone-500 dark:text-stone-400">{children}</div>}
    </div>
  );
}
