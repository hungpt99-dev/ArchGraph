import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight, Activity, Boxes, Braces, Database, FileText, GitBranch, Layers, Network,
  Plus, RefreshCw, Sparkles, Workflow,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { AddRepositoryModal } from "../components/AddRepository";
import { Badge, Button, Card, CardHeader, Empty, Progress, Spinner, Stat, cn, fmt, useToast } from "../components/ui";
import { get, post } from "../lib/api";

export function WorkspaceOverview() {
  const { workspaceId = "" } = useParams();
  const toast = useToast();
  const [addRepo, setAddRepo] = useState(false);
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["overview", workspaceId],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/overview`),
    refetchInterval: 15000,
  });

  if (isLoading || !data) return <Spinner label="Loading workspace…" />;
  const stats = data.stats;
  const counts = stats.node_counts || {};

  const scanAll = async () => {
    try {
      const out = await post<any>(`/workspaces/${workspaceId}/sync/all`, { mode: "full" });
      toast.push(`${out.count} scan job(s) queued`, "success");
      setTimeout(() => refetch(), 1200);
    } catch (error: any) {
      toast.push(error.message, "error");
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-white">{data.workspace.name}</h1>
            <Badge tone="SYNCED">system graph</Badge>
          </div>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">{data.workspace.description}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => setAddRepo(true)}><Plus size={14} /> Repository</Button>
          <Button variant="primary" onClick={scanAll}><RefreshCw size={14} /> Scan all</Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Services" value={fmt.number(counts.SERVICE)} hint={`${data.health.repositories.length} repositories`} icon={<Boxes size={15} />} tone="accent" />
        <Stat label="API endpoints" value={fmt.number(counts.API_ENDPOINT)} hint="REST, discovered statically" icon={<Braces size={15} />} />
        <Stat label="Kafka topics" value={fmt.number(counts.KAFKA_TOPIC)} hint={`${fmt.number(counts.PRODUCER)} producers · ${fmt.number(counts.CONSUMER)} consumers`} icon={<Activity size={15} />} />
        <Stat label="Dependencies" value={fmt.number(stats.edge_counts?.DEPENDS_ON)} hint={`${fmt.number(stats.edges)} graph edges`} icon={<Network size={15} />} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <Card>
          <CardHeader title="Services" subtitle="Discovered from Spring Boot entrypoints, package manifests and framework config"
            icon={<Boxes size={15} />} actions={<Link to={`/w/${workspaceId}/services`} className="text-xs text-accent-soft hover:underline">View all</Link>} />
          <div className="divide-y divide-white/5">
            {data.services.map((s: any) => (
              <Link key={s.id} to={`/w/${workspaceId}/services/${s.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-white/[0.03]">
                <div className="grid h-8 w-8 place-items-center rounded-lg bg-accent/10 text-accent-soft"><Boxes size={14} /></div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-100">{s.name}</span>
                    <Badge tone={s.language === "java" ? "QUEUED" : "GENERATED"}>{s.language || "unknown"}</Badge>
                  </div>
                  <div className="mt-0.5 flex gap-3 text-[11px] text-slate-500">
                    <span>{s.api_count} {s.api_count === 1 ? "endpoint" : "endpoints"}</span>
                    <span>{s.dependency_count} {s.dependency_count === 1 ? "dependency" : "dependencies"}</span>
                    <span>{s.repository_name}</span>
                  </div>
                </div>
                <ArrowRight size={14} className="text-slate-600" />
              </Link>
            ))}
            {data.services.length === 0 && (
              <div className="p-6"><Empty title="No services indexed yet" hint="Add a repository and queue a scan." /></div>
            )}
          </div>
        </Card>

        <div className="space-y-4">
          <Card>
            <CardHeader title="Scan health" icon={<GitBranch size={15} />} actions={<Link to={`/w/${workspaceId}/sync`} className="text-xs text-accent-soft hover:underline">History</Link>} />
            <div className="space-y-3 p-4">
              {data.health.repositories.map((r: any) => (
                <div key={r.id}>
                  <div className="flex items-center justify-between text-xs">
                    <span className="truncate text-slate-200">{r.name}</span>
                    <div className="flex items-center gap-2">
                      <span className="mono text-[10px] text-slate-500">{fmt.commit(r.last_commit)}</span>
                      <Badge>{r.status}</Badge>
                    </div>
                  </div>
                  <div className="mt-1 text-[10px] text-slate-500">
                    {r.language || "—"} · synced {fmt.ago(r.last_synced_at)}
                    {r.error && <span className="text-rose-400"> · {r.error.slice(0, 80)}</span>}
                  </div>
                </div>
              ))}
              {data.health.repositories.length === 0 && <p className="text-xs text-slate-500">No repositories yet.</p>}
              {data.health.failed_scans > 0 && <p className="text-[11px] text-rose-300">{data.health.failed_scans} failed scan(s) need attention.</p>}
            </div>
          </Card>

          <Card>
            <CardHeader title="AI enrichment" subtitle="Optional layer — never required" icon={<Sparkles size={15} />}
              actions={<Link to={`/w/${workspaceId}/ai`} className="text-xs text-accent-soft hover:underline">Open</Link>} />
            <div className="grid grid-cols-3 gap-2 p-4 text-center">
              {["APPROVED", "NEEDS_REVIEW", "STALE"].map((status) => (
                <div key={status} className="rounded-lg border border-white/5 bg-white/[0.02] px-2 py-3">
                  <div className="text-lg font-semibold text-slate-100">{data.ai[status] || 0}</div>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">{status.replace("_", " ")}</div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Business flows" subtitle="Derived deterministically from producers and consumers" icon={<Workflow size={15} />}
            actions={<Link to={`/w/${workspaceId}/flows`} className="text-xs text-accent-soft hover:underline">All flows</Link>} />
          <div className="divide-y divide-white/5">
            {data.flows.map((f: any) => (
              <div key={f.id} className="flex items-center gap-3 px-5 py-3">
                <Badge tone={f.detection_method === "AI_INFERRED" ? "AI_INFERRED" : "GENERATED"}>
                  {f.detection_method === "AI_INFERRED" ? "inferred" : "deterministic"}
                </Badge>
                <span className="truncate text-sm text-slate-200">{f.name}</span>
                <span className="ml-auto text-[11px] text-slate-500">{f.steps.length} steps</span>
              </div>
            ))}
            {data.flows.length === 0 && <p className="p-5 text-xs text-slate-500">No cross-service flows found.</p>}
          </div>
        </Card>

        <Card>
          <CardHeader title="Recent scanning activity" icon={<Activity size={15} />} />
          <div className="divide-y divide-white/5">
            {data.recent_scans.map((s: any) => (
              <div key={s.id} className="flex items-center gap-3 px-5 py-3 text-xs">
                <Badge>{s.status}</Badge>
                <span className="text-slate-300">{s.mode}</span>
                <span className="text-slate-500">{s.trigger}</span>
                <span className="mono ml-auto text-[10px] text-slate-500">{fmt.commit(s.commit)}</span>
                <span className="w-16 text-right text-slate-500">{fmt.ago(s.finished_at || s.created_at)}</span>
              </div>
            ))}
            {data.recent_scans.length === 0 && <p className="p-5 text-xs text-slate-500">No scan runs yet.</p>}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Knowledge graph composition" subtitle={`${fmt.number(stats.nodes)} active nodes · ${fmt.number(stats.unresolved_edges)} unresolved references`} icon={<Network size={15} />} />
        <div className="grid gap-3 p-5 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(counts).sort((a, b) => (b[1] as number) - (a[1] as number)).slice(0, 8).map(([type, count]) => {
            const max = Math.max(...Object.values(counts).map((v) => v as number));
            return (
              <div key={type} className="rounded-lg border border-white/5 bg-white/[0.02] p-3">
                <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
                  <span>{type.replaceAll("_", " ")}</span><span className="text-slate-300">{count as number}</span>
                </div>
                <div className="mt-2"><Progress value={(count as number) / max * 100} /></div>
              </div>
            );
          })}
        </div>
      </Card>

      <AddRepositoryModal workspaceId={workspaceId} open={addRepo} onOpenChange={setAddRepo} onAdded={() => setTimeout(() => refetch(), 1500)} />
    </div>
  );
}
