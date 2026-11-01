import { useQuery } from "@tanstack/react-query";
import {
  Activity, BookOpen, Boxes, Braces, ChevronDown, ChevronRight, GitBranch, LayoutDashboard,
  LogOut, Menu, Network, Search, Settings, ShieldCheck, Sparkles, Workflow, X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, NavLink, useNavigate, useParams } from "react-router-dom";
import { get } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Badge, Button, cn, Input } from "./ui";

const NAV = [
  { to: "", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "services", label: "Services", icon: Boxes },
  { to: "explorer", label: "Architecture", icon: Network },
  { to: "flows", label: "Business flows", icon: Workflow },
  { to: "events", label: "APIs & Events", icon: Braces },
  { to: "docs", label: "Documentation", icon: BookOpen },
  { to: "ai", label: "AI Intelligence", icon: Sparkles },
  { to: "integrations", label: "Integrations", icon: GitBranch },
  { to: "sync", label: "Synchronization", icon: Activity },
  { to: "settings", label: "Settings", icon: Settings },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const { user, workspaces, logout } = useAuth();
  const { workspaceId } = useParams();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [switcher, setSwitcher] = useState(false);
  const [search, setSearch] = useState("");

  const current = workspaces.find((w) => w.id === workspaceId);
  const health = useQuery({
    queryKey: ["health", workspaceId],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/overview`),
    enabled: Boolean(workspaceId),
    refetchInterval: 30000,
  });

  useEffect(() => { setMobileOpen(false); }, [workspaceId]);

  const submitSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (search.trim() && workspaceId) navigate(`/w/${workspaceId}/explorer?q=${encodeURIComponent(search.trim())}`);
  };

  return (
    <div className="flex min-h-screen">
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-white/5 bg-ink-950/95 backdrop-blur transition-transform lg:static lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center gap-2.5 border-b border-white/5 px-4">
          <Link to="/" className="flex items-center gap-2.5" aria-label="ArchGraph home">
            <Logo />
            <div>
              <div className="text-sm font-semibold tracking-tight text-white">ArchGraph</div>
              <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Microservices Intelligence</div>
            </div>
          </Link>
          <button className="ml-auto text-slate-500 lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Close navigation">
            <X size={18} />
          </button>
        </div>

        <div className="relative px-3 py-3">
          <button
            onClick={() => setSwitcher((v) => !v)}
            className="flex w-full items-center justify-between gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-left hover:bg-white/[0.06] focus-ring"
          >
            <div className="min-w-0">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Workspace</div>
              <div className="truncate text-sm font-medium text-slate-100">{current?.name || "Select workspace"}</div>
            </div>
            <ChevronDown size={14} className="text-slate-500" />
          </button>
          {switcher && (
            <div className="absolute left-3 right-3 top-full z-40 mt-1 max-h-72 overflow-y-auto rounded-lg border border-white/10 bg-ink-850 p-1 shadow-2xl">
              {workspaces.length === 0 && <div className="px-3 py-2 text-xs text-slate-500">No workspaces yet</div>}
              {workspaces.map((w) => (
                <button
                  key={w.id}
                  onClick={() => { setSwitcher(false); navigate(`/w/${w.id}`); }}
                  className={cn("flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-xs hover:bg-white/5",
                    w.id === workspaceId ? "text-white" : "text-slate-300")}
                >
                  <span className="truncate">{w.name}</span>
                  {w.id === workspaceId && <ChevronRight size={13} />}
                </button>
              ))}
              <Link to="/" onClick={() => setSwitcher(false)} className="mt-1 block rounded-md px-3 py-2 text-xs text-accent-soft hover:bg-white/5">
                View all workspaces
              </Link>
            </div>
          )}
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-2 pb-4">
          {NAV.map((item) => {
            const href = workspaceId ? `/w/${workspaceId}/${item.to}`.replace(/\/$/, "") : "/";
            const disabled = !workspaceId && item.to !== "";
            return (
              <NavLink
                key={item.label}
                to={disabled ? "/" : href}
                end={item.end}
                onClick={(e) => { if (disabled) e.preventDefault(); }}
                className={({ isActive }) =>
                  cn("flex items-center gap-2.5 rounded-lg px-3 py-2 text-[13px] transition",
                    isActive && !disabled ? "bg-accent/12 text-white shadow-[inset_0_0_0_1px_rgba(99,102,241,.25)]" : "text-slate-400 hover:bg-white/5 hover:text-slate-200",
                    disabled && "opacity-40 cursor-not-allowed")
                }
              >
                <item.icon size={15} className="shrink-0" />
                <span className="truncate">{item.label}</span>
                {item.to === "ai" && health.data?.ai && (
                  <span className="ml-auto rounded bg-amber-500/15 px-1.5 text-[10px] text-amber-300">
                    {(health.data.ai.NEEDS_REVIEW || 0) + (health.data.ai.QUEUED || 0) || ""}
                  </span>
                )}
              </NavLink>
            );
          })}
        </nav>

        <div className="border-t border-white/5 p-3">
          <div className="mb-2 flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-accent/20 text-xs font-semibold text-accent-soft">
              {user?.name?.slice(0, 1).toUpperCase() || "?"}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs text-slate-200">{user?.name}</div>
              <div className="truncate text-[10px] text-slate-500">{user?.role}</div>
            </div>
            <button onClick={logout} title="Sign out" className="text-slate-500 hover:text-rose-300 focus-ring">
              <LogOut size={14} />
            </button>
          </div>
          <div className="flex items-center gap-1.5 px-1 text-[10px] text-slate-600">
            <ShieldCheck size={11} /> Deterministic graph · AI optional
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-white/5 bg-ink-950/80 px-4 backdrop-blur">
          <button className="text-slate-400 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu size={20} />
          </button>
          <form onSubmit={submitSearch} className="relative max-w-md flex-1">
            <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search services, classes, endpoints…"
              className="h-9 pl-9"
              aria-label="Search the architecture"
            />
          </form>
          <div className="ml-auto flex items-center gap-2">
            {health.data && (
              <>
                <Badge tone={health.data.health?.repositories?.some((r: any) => r.status === "ERROR") ? "FAILED" : "ACTIVE"}>
                  {health.data.health?.repositories?.filter((r: any) => r.status === "READY").length || 0}/
                  {health.data.health?.repositories?.length || 0} repos synced
                </Badge>
                <Link to={`/w/${workspaceId}/ai`} className="hidden sm:block">
                  <Badge tone="MOCK">AI {health.data.ai?.APPROVED ? "approved" : "optional"}</Badge>
                </Link>
              </>
            )}
            <Button size="sm" variant="outline" onClick={() => navigate(`/w/${workspaceId}/sync`)}>
              Sync
            </Button>
          </div>
        </header>
        <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8">{children}</main>
      </div>
      {mobileOpen && <div className="fixed inset-0 z-20 bg-black/60 lg:hidden" onClick={() => setMobileOpen(false)} />}
    </div>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="8" fill="#0b0f18" stroke="url(#g)" strokeWidth="1.2" />
      <circle cx="9" cy="22" r="3" fill="#22d3ee" />
      <circle cx="16" cy="10" r="3" fill="#818cf8" />
      <circle cx="24" cy="20" r="3" fill="#6366f1" />
      <path d="M10.5 19.5 L14.6 12.4 M17.7 12.2 L22.3 17.8 M11.8 21.4 L21.4 20.4" stroke="#64748b" strokeWidth="1.1" />
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="32" y2="32">
          <stop stopColor="#6366f1" /><stop offset="1" stopColor="#22d3ee" stopOpacity=".6" />
        </linearGradient>
      </defs>
    </svg>
  );
}
