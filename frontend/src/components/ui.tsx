import * as Dialog from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { AlertTriangle, Check, ChevronDown, Loader2, X } from "lucide-react";
import React, { createContext, useCallback, useContext, useMemo, useState } from "react";

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/* ------------------------------------------------------------------ buttons */
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md";
  loading?: boolean;
};

export function Button({ variant = "secondary", size = "md", loading, className, children, ...rest }: ButtonProps) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus-ring disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap";
  const variants = {
    primary: "bg-accent text-white hover:bg-accent/90 shadow-[0_6px_24px_-10px_rgba(99,102,241,.9)]",
    secondary: "bg-white/5 text-slate-200 hover:bg-white/10 border border-white/10",
    ghost: "text-slate-300 hover:bg-white/5",
    outline: "border border-white/15 text-slate-200 hover:border-accent/60 hover:text-white",
    danger: "bg-rose-500/10 text-rose-300 border border-rose-500/30 hover:bg-rose-500/20",
  };
  const sizes = { sm: "h-8 px-3 text-xs", md: "h-9 px-4 text-sm py-2" };
  return (
    <button className={cn(base, variants[variant], sizes[size], className)} disabled={loading || rest.disabled} {...rest}>
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  );
}

/* -------------------------------------------------------------------- cards */
export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("card", className)} {...rest}>{children}</div>;
}

export function CardHeader({ title, subtitle, actions, icon }: {
  title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode; icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-white/5 px-5 py-4">
      <div className="flex items-start gap-3 min-w-0">
        {icon && <div className="mt-0.5 text-accent-soft">{icon}</div>}
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-100 truncate">{title}</h3>
          {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, tone = "default", icon }: {
  label: string; value: React.ReactNode; hint?: string; tone?: "default" | "accent" | "warn" | "ok";
  icon?: React.ReactNode;
}) {
  const tones = {
    default: "text-slate-100",
    accent: "text-accent-soft",
    warn: "text-amber-300",
    ok: "text-emerald-300",
  };
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        {icon && <span className="text-slate-500">{icon}</span>}
      </div>
      <div className={cn("mt-2 text-2xl font-semibold tracking-tight tabular-nums", tones[tone])}>{value}</div>
      {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
    </Card>
  );
}

/* ------------------------------------------------------------------- badges */
const STATUS_TONES: Record<string, string> = {
  ok: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  green: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  ACTIVE: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  READY: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  COMPLETED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  APPROVED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  CONNECTED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/25",
  GENERATED: "bg-sky-500/10 text-sky-300 border-sky-500/25",
  SYNCED: "bg-sky-500/10 text-sky-300 border-sky-500/25",
  RUNNING: "bg-sky-500/10 text-sky-300 border-sky-500/25",
  QUEUED: "bg-indigo-500/10 text-indigo-300 border-indigo-500/25",
  ANALYZING: "bg-indigo-500/10 text-indigo-300 border-indigo-500/25",
  NEEDS_REVIEW: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  STALE: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  WARN: "bg-amber-500/10 text-amber-300 border-amber-500/25",
  SKIPPED: "bg-slate-500/10 text-slate-300 border-slate-500/25",
  REMOVED: "bg-slate-500/10 text-slate-400 border-slate-500/25",
  REJECTED: "bg-rose-500/10 text-rose-300 border-rose-500/25",
  FAILED: "bg-rose-500/10 text-rose-300 border-rose-500/25",
  ERROR: "bg-rose-500/10 text-rose-300 border-rose-500/25",
  DEAD: "bg-rose-500/10 text-rose-300 border-rose-500/25",
  PENDING: "bg-slate-500/10 text-slate-300 border-slate-500/25",
  PING: "bg-slate-500/10 text-slate-300 border-slate-500/25",
  IGNORED: "bg-slate-500/10 text-slate-400 border-slate-500/25",
  AI_INFERRED: "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/25",
  MOCK: "bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/25",
};

export function Badge({ children, tone, className }: { children: React.ReactNode; tone?: string; className?: string }) {
  const key = typeof children === "string" ? children : tone || "";
  const cls = STATUS_TONES[key] || STATUS_TONES[tone || ""] || "bg-white/5 text-slate-300 border-white/10";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide", cls, className)}>
      {children}
    </span>
  );
}

export function Dot({ tone = "slate" }: { tone?: "green" | "red" | "amber" | "slate" | "indigo" }) {
  const map = { green: "bg-emerald-400", red: "bg-rose-400", amber: "bg-amber-400", slate: "bg-slate-500", indigo: "bg-indigo-400" };
  return <span className={cn("inline-block h-1.5 w-1.5 rounded-full", map[tone])} />;
}

/* ------------------------------------------------------------------- inputs */
export function Input({ className, ...rest }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-slate-100 placeholder:text-slate-500 focus-ring",
        className,
      )}
      {...rest}
    />
  );
}

export function Textarea({ className, ...rest }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn("w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus-ring", className)}
      {...rest}
    />
  );
}

export function Select({ className, children, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={cn("h-9 w-full appearance-none rounded-lg border border-white/10 bg-ink-950/60 px-3 pr-8 text-sm text-slate-100 focus-ring", className)}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown size={14} className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
    </div>
  );
}

export function Field({ label, hint, children, required }: { label: string; hint?: string; children: React.ReactNode; required?: boolean }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-slate-300">
        {label} {required && <span className="text-rose-400">*</span>}
      </span>
      {children}
      {hint && <span className="block text-[11px] text-slate-500">{hint}</span>}
    </label>
  );
}

/* --------------------------------------------------------------------- tabs */
export const Tabs = TabsPrimitive.Root;
export function TabsList({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <TabsPrimitive.List className={cn("inline-flex items-center gap-1 rounded-lg border border-white/10 bg-ink-950/60 p-1", className)}>
      {children}
    </TabsPrimitive.List>
  );
}
export function TabsTrigger({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <TabsPrimitive.Trigger
      value={value}
      className="rounded-md px-3 py-1.5 text-xs font-medium text-slate-400 transition data-[state=active]:bg-white/10 data-[state=active]:text-white focus-ring"
    >
      {children}
    </TabsPrimitive.Trigger>
  );
}
export function TabsContent({ value, children, className }: { value: string; children: React.ReactNode; className?: string }) {
  return <TabsPrimitive.Content value={value} className={cn("mt-4 focus:outline-none", className)}>{children}</TabsPrimitive.Content>;
}

/* -------------------------------------------------------------------- modal */
export function Modal({ open, onOpenChange, title, description, children, footer, width = "max-w-xl" }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: string;
  children: React.ReactNode; footer?: React.ReactNode; width?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/70 backdrop-blur-sm data-[state=open]:animate-in" />
        <Dialog.Content
          className={cn("fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-white/10 bg-ink-900 p-5 shadow-2xl", width)}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-base font-semibold text-white">{title}</Dialog.Title>
              {description && <Dialog.Description className="mt-1 text-xs text-slate-400">{description}</Dialog.Description>}
            </div>
            <Dialog.Close className="rounded-md p-1 text-slate-400 hover:bg-white/5 hover:text-white focus-ring" aria-label="Close">
              <X size={16} />
            </Dialog.Close>
          </div>
          <div className="max-h-[70vh] overflow-y-auto pr-1">{children}</div>
          {footer && <div className="mt-5 flex justify-end gap-2 border-t border-white/5 pt-4">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/* ------------------------------------------------------------------ toaster */
type Toast = { id: number; message: string; tone: "success" | "error" | "info" };
const ToastCtx = createContext<{ push: (message: string, tone?: Toast["tone"]) => void }>({ push: () => {} });
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, message, tone }]);
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 4200);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="fixed bottom-4 right-4 z-[100] flex w-[min(92vw,380px)] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id}
            className={cn("flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-xl backdrop-blur",
              t.tone === "success" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-200",
              t.tone === "error" && "border-rose-500/30 bg-rose-500/10 text-rose-200",
              t.tone === "info" && "border-white/10 bg-ink-800/95 text-slate-200")}>
            {t.tone === "success" ? <Check size={16} className="mt-0.5" /> : <AlertTriangle size={16} className="mt-0.5" />}
            <span className="leading-snug">{t.message}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

/* ------------------------------------------------------------------- misc */
export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-400">
      <Loader2 size={16} className="animate-spin" /> {label || "Loading…"}
    </div>
  );
}

export function Empty({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-white/10 px-6 py-12 text-center">
      <p className="text-sm font-medium text-slate-300">{title}</p>
      {hint && <p className="max-w-md text-xs text-slate-500">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Progress({ value, tone = "accent" }: { value: number; tone?: "accent" | "green" | "amber" }) {
  const tones = { accent: "bg-accent", green: "bg-emerald-400", amber: "bg-amber-400" };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div className={cn("h-full rounded-full transition-all", tones[tone])} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function KeyValue({ items }: { items: Array<[string, React.ReactNode]> }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-[10px] uppercase tracking-wider text-slate-500">{k}</dt>
          <dd className="truncate text-xs text-slate-200">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export { fmt } from "../lib/api";
