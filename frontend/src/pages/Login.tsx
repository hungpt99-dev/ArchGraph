import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ShieldCheck, Sparkles, Terminal } from "lucide-react";
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../components/Layout";
import { Button, Card, Field, Input, useToast } from "../components/ui";
import { get, post } from "../lib/api";
import { useAuth } from "../lib/auth";

export function Login() {
  const { user, login, bootstrap } = useAuth();
  const navigate = useNavigate();
  const location = useLocation() as { state?: { from?: string } };
  const toast = useToast();
  const [mode, setMode] = useState<"login" | "bootstrap">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  const status = useQuery({ queryKey: ["auth-status"], queryFn: () => get<any>("/auth/status") });

  useEffect(() => {
    if (status.data?.bootstrap_required) setMode("bootstrap");
  }, [status.data]);

  useEffect(() => {
    if (user) navigate(location.state?.from || "/", { replace: true });
  }, [user]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      if (mode === "bootstrap") {
        await bootstrap(email, name, password);
        toast.push("Administrator account created", "success");
      } else {
        await login(email, password);
      }
      navigate(location.state?.from || "/", { replace: true });
    } catch (error: any) {
      toast.push(error.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-white/5 p-12 lg:flex">
        <div className="absolute inset-0 opacity-[0.35]" style={{
          backgroundImage: "linear-gradient(rgba(99,102,241,.12) 1px, transparent 1px), linear-gradient(90deg, rgba(99,102,241,.12) 1px, transparent 1px)",
          backgroundSize: "44px 44px",
          maskImage: "radial-gradient(ellipse at 30% 20%, black 20%, transparent 70%)",
        }} />
        <div className="relative flex items-center gap-3">
          <Logo size={40} />
          <div>
            <div className="text-lg font-semibold tracking-tight text-white">ArchGraph</div>
            <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Microservices Intelligence</div>
          </div>
        </div>
        <div className="relative max-w-lg">
          <h1 className="text-4xl font-semibold leading-[1.1] tracking-tight text-white">
            Understand your entire system.<br />
            <span className="text-accent-soft">Not just your code.</span>
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-400">
            A unified, evidence-backed graph of every service, endpoint, Kafka topic, table and
            document — with optional, selective AI enrichment that never overwrites deterministic facts.
          </p>
          <div className="mt-8 grid gap-3 text-xs text-slate-400">
            <Feature icon={<ShieldCheck size={14} />} text="Deterministic scanner: Java, TypeScript, Python, Go, SQL. No LLM required." />
            <Feature icon={<Sparkles size={14} />} text="AI enriches only what you select, with approval and staleness tracking." />
            <Feature icon={<Terminal size={14} />} text="37 MCP tools so Claude Code, Codex and Cursor can query the graph." />
          </div>
        </div>
        <div className="relative text-[11px] text-slate-600">Self-hosted · PostgreSQL · Docker Compose</div>
      </section>

      <section className="flex items-center justify-center p-6">
        <Card className="w-full max-w-md p-7">
          <div className="mb-6 flex items-center gap-3 lg:hidden">
            <Logo size={32} />
            <span className="text-base font-semibold text-white">ArchGraph</span>
          </div>
          <h2 className="text-xl font-semibold text-white">
            {mode === "bootstrap" ? "Create the administrator" : "Sign in"}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {mode === "bootstrap"
              ? "This instance has no users yet. The first account is the system administrator."
              : "Use your ArchGraph credentials to open the admin dashboard."}
          </p>
          <form onSubmit={submit} className="mt-6 space-y-4">
            {mode === "bootstrap" && (
              <Field label="Your name" required>
                <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Ada Lovelace" autoComplete="name" />
              </Field>
            )}
            <Field label="Email" required>
              <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" autoComplete="username" />
            </Field>
            <Field label="Password" hint={mode === "bootstrap" ? "Minimum 8 characters." : undefined} required>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" autoComplete={mode === "bootstrap" ? "new-password" : "current-password"} />
            </Field>
            <Button type="submit" variant="primary" className="w-full" loading={busy}
              disabled={!email || password.length < 8 || (mode === "bootstrap" && name.length < 2)}>
              {mode === "bootstrap" ? "Create admin & enter" : "Sign in"} <ArrowRight size={14} />
            </Button>
          </form>
          {status.data && !status.data.bootstrap_required && (
            <p className="mt-4 text-center text-[11px] text-slate-500">
              Demo credentials: <span className="mono text-slate-300">admin@archgraph.io</span> / <span className="mono text-slate-300">archgraph-demo-2026</span>
            </p>
          )}
        </Card>
      </section>
    </div>
  );
}

function Feature({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 text-accent-soft">{icon}</span>
      <span>{text}</span>
    </div>
  );
}
