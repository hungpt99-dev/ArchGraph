import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, GitCommitHorizontal, RefreshCw, RotateCcw, Webhook } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, CardHeader, Empty, Progress, Spinner, Tabs, TabsContent, TabsList, TabsTrigger, fmt, useToast } from "../components/ui";
import { get, post } from "../lib/api";

export function Sync() {
  const { workspaceId = "" } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);

  const events = useQuery({
    queryKey: ["sync-events", workspaceId],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/sync/events`),
    refetchInterval: 5000,
  });
  const repos = useQuery({ queryKey: ["repos", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/repositories`) });

  const scanAll = async (mode: string) => {
    setBusy(true);
    try {
      const out = await post<any>(`/workspaces/${workspaceId}/sync/all`, { mode });
      toast.push(`${out.count} job(s) queued`, "success");
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const retry = async (id: string) => {
    try { await post(`/jobs/${id}/retry`); toast.push("Job requeued", "success"); await qc.invalidateQueries(); }
    catch (e: any) { toast.push(e.message, "error"); }
  };

  const scanRepo = async (id: string, mode: string) => {
    try { await post(`/repositories/${id}/scan`, { mode }); toast.push("Scan queued", "success"); await qc.invalidateQueries(); }
    catch (e: any) { toast.push(e.message, "error"); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Synchronization</h1>
          <p className="mt-1 text-xs text-slate-400">Scans, webhook deliveries and background jobs. Incremental scans only re-index changed files.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" loading={busy} onClick={() => scanAll("incremental")}><RefreshCw size={14} /> Incremental all</Button>
          <Button variant="primary" loading={busy} onClick={() => scanAll("full")}><RefreshCw size={14} /> Full re-index</Button>
        </div>
      </div>

      <Card>
        <CardHeader title="Repositories" subtitle="Latest indexed commit per repository" icon={<GitCommitHorizontal size={15} />} />
        <div className="divide-y divide-white/5">
          {repos.data?.map((repo) => (
            <div key={repo.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-slate-200">{repo.name}</span>
                  <Badge tone={repo.status}>{repo.status}</Badge>
                </div>
                <div className="mt-0.5 flex gap-3 text-[10px] text-slate-500">
                  <span className="mono">{fmt.commit(repo.last_commit)}</span>
                  <span>{repo.branch}</span>
                  <span>{Object.keys(repo.languages || {}).join(", ") || "—"}</span>
                  <span>synced {fmt.ago(repo.last_synced_at)}</span>
                </div>
              </div>
              <Button size="sm" variant="outline" onClick={() => scanRepo(repo.id, "incremental")}>Incremental</Button>
              <Button size="sm" variant="ghost" onClick={() => scanRepo(repo.id, "full")}>Full</Button>
            </div>
          ))}
          {repos.data?.length === 0 && <p className="p-5 text-xs text-slate-500">No repositories.</p>}
        </div>
      </Card>

      <Tabs defaultValue="jobs">
        <TabsList>
          <TabsTrigger value="jobs">Jobs ({events.data?.jobs?.length || 0})</TabsTrigger>
          <TabsTrigger value="scans">Scan runs ({events.data?.scans?.length || 0})</TabsTrigger>
          <TabsTrigger value="webhooks">Webhooks ({events.data?.webhooks?.length || 0})</TabsTrigger>
        </TabsList>

        <TabsContent value="jobs">
          <Card>
            <div className="divide-y divide-white/5">
              {events.data?.jobs?.map((job: any) => (
                <div key={job.id} className="px-5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={job.status}>{job.status}</Badge>
                    <span className="text-xs text-slate-200">{job.kind}</span>
                    <span className="text-[10px] text-slate-500">attempt {job.attempts} · {fmt.ago(job.created_at)}</span>
                    {["DEAD", "FAILED", "COMPLETED"].includes(job.status) && (
                      <button onClick={() => retry(job.id)} className="ml-auto inline-flex items-center gap-1 text-[10px] text-accent-soft hover:underline">
                        <RotateCcw size={11} /> retry
                      </button>
                    )}
                  </div>
                  <div className="mt-1.5"><Progress value={job.progress} tone={job.status === "DEAD" ? "amber" : "accent"} /></div>
                  {job.error && <p className="mt-1 text-[10px] text-rose-300">{job.error}</p>}
                </div>
              ))}
              {events.data?.jobs?.length === 0 && <div className="p-6"><Empty title="No jobs yet" /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="scans">
          <Card>
            <div className="divide-y divide-white/5">
              {events.data?.scans?.map((s: any) => (
                <div key={s.id} className="flex flex-wrap items-center gap-2 px-5 py-3 text-[11px]">
                  <Badge tone={s.status}>{s.status}</Badge>
                  <span className="text-slate-300">{s.mode}</span>
                  <span className="text-slate-500">{s.trigger}</span>
                  <span className="mono text-[10px] text-slate-500">{fmt.commit(s.commit)}</span>
                  <span className="ml-auto text-[10px] text-slate-500">
                    {s.stats?.files_scanned ? `${s.stats.files_scanned} files · ` : ""}
                    {s.stats?.nodes_created != null ? `${s.stats.nodes_created} created · ` : ""}
                    {s.stats?.duration_seconds ? `${s.stats.duration_seconds}s` : ""}
                  </span>
                  <span className="w-20 text-right text-[10px] text-slate-500">{fmt.ago(s.finished_at || s.created_at)}</span>
                </div>
              ))}
              {events.data?.scans?.length === 0 && <div className="p-6"><Empty title="No scans yet" /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="webhooks">
          <Card>
            <div className="divide-y divide-white/5">
              {events.data?.webhooks?.map((h: any) => (
                <div key={h.id} className="flex flex-wrap items-center gap-2 px-5 py-3 text-[11px]">
                  <Webhook size={13} className="text-cyan-300" />
                  <Badge tone={h.status === "QUEUED" ? "QUEUED" : h.status === "IGNORED" ? "PENDING" : "ACTIVE"}>{h.status}</Badge>
                  <span className="text-slate-300">{h.provider}</span>
                  <span className="text-slate-500">{h.event_type}</span>
                  <span className="mono ml-auto text-[10px] text-slate-600">{h.delivery_id?.slice(0, 18)}</span>
                  <span className="w-20 text-right text-[10px] text-slate-500">{fmt.ago(h.received_at)}</span>
                </div>
              ))}
              {events.data?.webhooks?.length === 0 && (
                <div className="p-6"><Empty title="No webhook deliveries" hint="Create a secret on the Integrations page and configure the provider." /></div>
              )}
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
