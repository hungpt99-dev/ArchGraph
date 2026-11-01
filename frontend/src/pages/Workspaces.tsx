import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Boxes, Braces, GitBranch, Layers, Network, Plus, Workflow } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Layout, Logo } from "../components/Layout";
import { Button, Card, Empty, Field, Input, Modal, Spinner, Stat, useToast } from "../components/ui";
import { fmt, get, post } from "../lib/api";
import { useAuth } from "../lib/auth";

export function Workspaces() {
  const { workspaces, refresh } = useAuth();
  const navigate = useNavigate();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    try {
      const ws = await post<any>("/workspaces", { name, description });
      await refresh();
      toast.push("Workspace created", "success");
      navigate(`/w/${ws.id}`);
    } catch (error: any) {
      toast.push(error.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen">
      <header className="border-b border-white/5">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-6 py-4">
          <Logo size={34} />
          <div>
            <div className="text-sm font-semibold text-white">ArchGraph</div>
            <div className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Understand your entire system</div>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Link to="/login"><Button size="sm" variant="ghost">Switch account</Button></Link>
            <Button size="sm" onClick={() => setOpen(true)}><Plus size={14} /> New workspace</Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-10">
        <div className="mb-8">
          <h1 className="text-2xl font-semibold tracking-tight text-white">Workspaces</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            A workspace is one complete software system: every repository, service, endpoint,
            topic and database, with the evidence behind each fact.
          </p>
        </div>

        {workspaces.length === 0 ? (
          <Empty
            title="No workspaces yet"
            hint="Create a workspace, add your repositories, and ArchGraph will scan them deterministically — no AI key required."
            action={<Button onClick={() => setOpen(true)}><Plus size={14} /> Create workspace</Button>}
          />
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {workspaces.map((ws) => <WorkspaceCard key={ws.id} ws={ws} />)}
          </div>
        )}
      </main>

      <Modal
        open={open}
        onOpenChange={setOpen}
        title="Create workspace"
        description="Repositories, documentation and AI policies live inside a workspace."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" loading={busy} disabled={name.trim().length < 2} onClick={create}>Create workspace</Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name" required>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Hotel Management Platform" autoFocus />
          </Field>
          <Field label="Description" hint="What system does this workspace represent?">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Reservations, payments, invoicing and notifications" />
          </Field>
        </div>
      </Modal>
    </div>
  );
}

function WorkspaceCard({ ws }: { ws: { id: string; name: string; slug: string } }) {
  const { data, isLoading } = useQuery({
    queryKey: ["workspace-card", ws.id],
    queryFn: () => get<any>(`/workspaces/${ws.id}/overview`),
  });
  return (
    <Link to={`/w/${ws.id}`} className="group block">
      <Card className="h-full p-5 transition group-hover:border-accent/40 group-hover:shadow-glow">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-white">{ws.name}</h3>
            <p className="mt-1 line-clamp-2 text-xs text-slate-400">{data?.workspace?.description || "No description"}</p>
          </div>
          <ArrowRight size={15} className="text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-accent-soft" />
        </div>
        {isLoading ? <Spinner label="" /> : (
          <div className="mt-5 grid grid-cols-2 gap-3 text-xs">
            <Metric icon={<Boxes size={13} />} label="Services" value={data?.stats?.node_counts?.SERVICE || 0} />
            <Metric icon={<Braces size={13} />} label="APIs" value={data?.stats?.node_counts?.API_ENDPOINT || 0} />
            <Metric icon={<Layers size={13} />} label="Kafka topics" value={data?.stats?.node_counts?.KAFKA_TOPIC || 0} />
            <Metric icon={<Network size={13} />} label="Graph edges" value={data?.stats?.edges || 0} />
          </div>
        )}
        <div className="mt-5 flex items-center justify-between border-t border-white/5 pt-3 text-[11px] text-slate-500">
          <span className="inline-flex items-center gap-1.5"><GitBranch size={12} />{data?.health?.repositories?.length || 0} repositories</span>
          <span>synced {fmt.ago(data?.health?.last_scan?.finished_at)}</span>
        </div>
      </Card>
    </Link>
  );
}

function Metric({ icon, label, value }: { icon: React.ReactNode; label: string; value: number }) {
  return (
    <div className="rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2">
      <div className="flex items-center gap-1.5 text-slate-500">{icon}<span className="text-[10px] uppercase tracking-wider">{label}</span></div>
      <div className="mt-1 text-lg font-semibold tabular-nums text-slate-100">{fmt.number(value)}</div>
    </div>
  );
}
