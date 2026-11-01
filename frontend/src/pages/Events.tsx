import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, Braces, Database, Link2, Search, Unlink } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Card, CardHeader, Empty, Input, Modal, Spinner, Tabs, TabsContent, TabsList, TabsTrigger, fmt } from "../components/ui";
import { get } from "../lib/api";

export function Events() {
  const { workspaceId = "" } = useParams();
  const [q, setQ] = useState("");
  const [trace, setTrace] = useState<string | null>(null);

  const apis = useQuery({ queryKey: ["apis", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/apis`) });
  const topics = useQuery({ queryKey: ["topics", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/topics`) });
  const tables = useQuery({ queryKey: ["tables", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/tables`) });
  const unresolved = useQuery({ queryKey: ["unresolved", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/unresolved`) });
  const traceData = useQuery({
    queryKey: ["trace", workspaceId, trace],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/topics/${encodeURIComponent(trace!)}/trace`),
    enabled: Boolean(trace),
  });

  const filter = (items: any[] = []) => items.filter((i) => !q || JSON.stringify(i.properties || i).toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">APIs & Events</h1>
          <p className="mt-1 text-xs text-slate-400">REST surface, Kafka topology and data access — all statically detected.</p>
        </div>
        <div className="relative w-64">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter…" className="pl-9" />
        </div>
      </div>

      <Tabs defaultValue="apis">
        <TabsList>
          <TabsTrigger value="apis">REST APIs ({apis.data?.length || 0})</TabsTrigger>
          <TabsTrigger value="topics">Kafka topics ({topics.data?.length || 0})</TabsTrigger>
          <TabsTrigger value="tables">Data stores ({tables.data?.length || 0})</TabsTrigger>
          <TabsTrigger value="unresolved">Unresolved ({unresolved.data?.length || 0})</TabsTrigger>
        </TabsList>

        <TabsContent value="apis">
          <Card>
            <CardHeader title="REST endpoints" subtitle="From Spring mapping annotations, NestJS decorators, Express routes and OpenAPI specs" icon={<Braces size={15} />} />
            <div className="divide-y divide-white/5">
              {apis.isLoading && <Spinner />}
              {filter(apis.data).map((api) => (
                <div key={api.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <Badge tone="GENERATED">{api.properties?.http_method || "ANY"}</Badge>
                  <span className="mono text-xs text-slate-200">{api.properties?.path || api.name}</span>
                  {api.properties?.auth?.length > 0 && <Badge tone="WARN">{api.properties.auth.join(", ")}</Badge>}
                  <span className="text-[10px] text-slate-500">{api.properties?.handler ? `${api.properties.handler}()` : ""}</span>
                  <span className="ml-auto text-[10px] text-slate-500">{api.detection_method} · {Math.round((api.confidence || 0) * 100)}%</span>
                </div>
              ))}
              {apis.data && filter(apis.data).length === 0 && <div className="p-6"><Empty title="No endpoints found" /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="topics">
          <div className="grid gap-3 lg:grid-cols-2">
            {topics.data?.map((t) => (
              <Card key={t.id} className="p-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2">
                    <Activity size={15} className="text-cyan-300" />
                    <span className="mono text-sm text-slate-100">{t.name}</span>
                  </div>
                  <Badge tone="ACTIVE">topic</Badge>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-3 text-[11px]">
                  <div>
                    <div className="label mb-1">Producers</div>
                    {t.producers.map((p: string) => <div key={p} className="text-slate-300">{p}</div>)}
                    {t.producers.length === 0 && <div className="text-slate-600">none detected</div>}
                  </div>
                  <div>
                    <div className="label mb-1">Consumers</div>
                    {t.consumers.map((c: string) => <div key={c} className="text-slate-300">{c}</div>)}
                    {t.consumers.length === 0 && <div className="text-slate-600">none detected</div>}
                  </div>
                </div>
                <button onClick={() => setTrace(t.name)} className="mt-3 inline-flex items-center gap-1 text-[11px] text-accent-soft hover:underline">
                  Trace event flow <ArrowRight size={11} />
                </button>
              </Card>
            ))}
            {topics.data?.length === 0 && <Empty title="No Kafka topics detected" />}
          </div>
        </TabsContent>

        <TabsContent value="tables">
          <Card>
            <CardHeader title="Databases & tables" subtitle="From datasource configuration, JPA entities and SQL migrations" icon={<Database size={15} />} />
            <div className="divide-y divide-white/5">
              {tables.data?.map((t) => (
                <div key={t.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <Badge tone={t.type === "DATABASE" ? "QUEUED" : "GENERATED"}>{t.type}</Badge>
                  <span className="text-xs text-slate-200">{t.name}</span>
                  {t.properties?.engine && <span className="text-[10px] text-slate-500">{t.properties.engine}</span>}
                  <span className="ml-auto text-[10px] text-slate-500">
                    {t.accessor_count ? `${t.accessor_count} accessor(s)` : ""} {t.services?.length ? `· ${t.services.join(", ")}` : ""}
                  </span>
                </div>
              ))}
              {tables.data?.length === 0 && <div className="p-6"><Empty title="No data stores detected" /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="unresolved">
          <Card>
            <CardHeader title="Unresolved references" subtitle="The scanner surfaces these instead of inventing a link" icon={<Unlink size={15} />} />
            <div className="divide-y divide-white/5">
              {unresolved.data?.map((u) => (
                <div key={u.id} className="flex flex-wrap items-center gap-3 px-5 py-2.5 text-[11px]">
                  <Badge tone="WARN">{u.type}</Badge>
                  <span className="text-slate-300">{u.src_name}</span>
                  <ArrowRight size={11} className="text-slate-600" />
                  <span className="mono text-slate-400">{u.dst_ref}</span>
                  <span className="ml-auto text-slate-600">{u.evidence?.file}:{u.evidence?.line}</span>
                </div>
              ))}
              {unresolved.data?.length === 0 && <div className="p-6"><Empty title="Everything resolved" hint="No dangling references in this workspace." /></div>}
            </div>
          </Card>
        </TabsContent>
      </Tabs>

      <Modal open={Boolean(trace)} onOpenChange={(v) => !v && setTrace(null)} title={`Event flow · ${trace}`} width="max-w-2xl"
        description="Producers, transport and consumers resolved across repositories.">
        {traceData.isLoading && <Spinner />}
        {traceData.data && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg border border-white/5 p-3">
                <div className="label mb-1">Producer services</div>
                {traceData.data.producers.map((p: any) => <div key={p.node.id} className="text-slate-200">{p.service || p.node.name}</div>)}
                {traceData.data.producers.length === 0 && <span className="text-slate-500">none</span>}
              </div>
              <div className="rounded-lg border border-white/5 p-3">
                <div className="label mb-1">Consumer services</div>
                {traceData.data.consumers.map((c: any) => <div key={c.node.id} className="text-slate-200">{c.service || c.node.name}</div>)}
                {traceData.data.consumers.length === 0 && <span className="text-slate-500">none</span>}
              </div>
            </div>
            <div>
              <div className="label mb-2">Flow</div>
              <div className="flex flex-wrap items-center gap-2">
                {traceData.data.steps.map((s: any, i: number) => (
                  <span key={i} className="flex items-center gap-2">
                    <span className="rounded-md border border-white/10 bg-white/[0.03] px-2 py-1 text-[11px] text-slate-200">
                      <span className="mr-1 text-[9px] uppercase text-slate-500">{s.stage}</span>{s.service || s.topic}
                    </span>
                    {i < traceData.data.steps.length - 1 && <ArrowRight size={11} className="text-slate-600" />}
                  </span>
                ))}
              </div>
            </div>
            <div className="text-[10px] text-slate-500">Topic evidence: {traceData.data.topic.evidence?.file} @ {fmt.commit(traceData.data.topic.evidence?.commit)}</div>
          </div>
        )}
      </Modal>
    </div>
  );
}
