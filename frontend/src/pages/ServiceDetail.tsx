import { useQuery } from "@tanstack/react-query";
import {
  Activity, ArrowLeft, ArrowRight, BookOpen, Boxes, Braces, Database, ExternalLink,
  GitBranch, Sparkles, Workflow,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, CardHeader, Empty, KeyValue, Spinner, Stat, Tabs, TabsContent, TabsList, TabsTrigger, fmt } from "../components/ui";
import { SourceViewer } from "../components/SourceViewer";
import { get } from "../lib/api";

export function ServiceDetail() {
  const { workspaceId = "", serviceId = "" } = useParams();
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["service", workspaceId, serviceId],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/services/${serviceId}`),
  });

  if (isLoading || !data) return <Spinner label="Loading service…" />;
  const arch = data.architecture;
  const service = arch.service;

  return (
    <div className="space-y-5">
      <Link to={`/w/${workspaceId}/services`} className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-white">
        <ArrowLeft size={13} /> All services
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-white">{service.name}</h1>
            <Badge tone="ACTIVE">service</Badge>
            {service.properties?.framework && <Badge tone="GENERATED">{service.properties.framework}</Badge>}
            {service.properties?.port && <Badge>:{service.properties.port}</Badge>}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {data.repository?.name || "unmapped"} · commit <span className="mono">{fmt.commit(data.repository?.commit)}</span> ·
            indexed {fmt.ago(service.last_seen)}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link to={`/w/${workspaceId}/explorer?node=${service.id}`} className="text-xs text-accent-soft hover:underline">Open in graph</Link>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Endpoints" value={data.counts.apis} icon={<Braces size={14} />} tone="accent" />
        <Stat label="Dependencies" value={data.counts.dependencies} icon={<ArrowRight size={14} />} />
        <Stat label="Kafka topics" value={data.counts.topics} icon={<Activity size={14} />} />
        <Stat label="Documents" value={arch.documents.length} icon={<BookOpen size={14} />} />
      </div>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="apis">APIs</TabsTrigger>
          <TabsTrigger value="events">Events</TabsTrigger>
          <TabsTrigger value="deps">Dependencies</TabsTrigger>
          <TabsTrigger value="code">Code</TabsTrigger>
          <TabsTrigger value="docs">Documentation</TabsTrigger>
          <TabsTrigger value="ai">AI insights</TabsTrigger>
        </TabsList>

        <TabsContent value="overview">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Service facts" subtitle="Every value is scanner-derived" icon={<Boxes size={15} />} />
              <div className="space-y-4 p-5">
                <KeyValue items={[
                  ["Qualified name", service.qualified_name],
                  ["Repository", data.repository?.name],
                  ["Branch", data.repository?.branch],
                  ["Revision", <span className="mono">{service.source_revision || data.repository?.commit}</span>],
                  ["Language", service.properties?.language],
                  ["Framework", service.properties?.framework],
                  ["Detection", service.detection_method],
                  ["Confidence", `${Math.round((service.confidence || 0) * 100)}%`],
                ]} />
                <div className="rounded-lg border border-white/5 bg-white/[0.02] p-3 text-[11px] text-slate-400">
                  <div className="mb-1 flex items-center gap-1.5 text-slate-300"><GitBranch size={12} /> Evidence</div>
                  <div className="mono text-[10px] text-slate-500">
                    {service.evidence?.file || "configuration"} · commit {fmt.commit(service.evidence?.commit)} · {service.evidence?.detection_method}
                  </div>
                </div>
              </div>
            </Card>

            <Card>
              <CardHeader title="Databases, caches & jobs" icon={<Database size={15} />} />
              <div className="space-y-2 p-4">
                {[...arch.databases, ...arch.caches, ...arch.jobs].map((n: any) => (
                  <div key={n.id} className="flex items-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2">
                    <Badge>{n.type.replaceAll("_", " ")}</Badge>
                    <span className="truncate text-xs text-slate-200">{n.name}</span>
                    <span className="ml-auto text-[10px] text-slate-500">{n.properties?.engine || n.properties?.cron || ""}</span>
                  </div>
                ))}
                {arch.databases.length + arch.caches.length + arch.jobs.length === 0 && (
                  <p className="text-xs text-slate-500">No database, cache or scheduled job detected.</p>
                )}
                {arch.classes.length > 0 && (
                  <div className="pt-2">
                    <div className="label mb-2">Classes in repository</div>
                    <div className="flex flex-wrap gap-1.5">
                      {arch.classes.slice(0, 14).map((c: any) => (
                        <button key={c.id} onClick={() => setSelectedNode(c.id)}
                          className="rounded-md border border-white/10 px-2 py-1 text-[10px] text-slate-300 hover:border-accent/50">
                          {c.name}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="apis">
          <Card>
            <CardHeader title="API surface" subtitle="REST endpoints exposed by this service" icon={<Braces size={15} />} />
            <div className="divide-y divide-white/5">
              {arch.apis.map((api: any) => (
                <div key={api.id} className="flex items-center gap-3 px-5 py-3">
                  <Badge tone="GENERATED">{api.properties?.http_method}</Badge>
                  <span className="mono text-xs text-slate-200">{api.properties?.path}</span>
                  <span className="ml-auto text-[10px] text-slate-500">{api.properties?.handler}()</span>
                  <Badge>{api.detection_method}</Badge>
                </div>
              ))}
              {arch.apis.length === 0 && <div className="p-5"><Empty title="No endpoints detected" /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="events">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Publishes" icon={<ArrowRight size={15} />} />
              <div className="space-y-2 p-4">
                {arch.publishes.map((t: any) => (
                  <div key={t.id} className="flex items-center gap-2 rounded-lg border border-white/5 px-3 py-2">
                    <Activity size={13} className="text-accent-soft" />
                    <span className="mono text-xs text-slate-200">{t.name}</span>
                  </div>
                ))}
                {arch.publishes.length === 0 && <p className="text-xs text-slate-500">No produced topics.</p>}
              </div>
            </Card>
            <Card>
              <CardHeader title="Consumes" icon={<ArrowLeft size={15} />} />
              <div className="space-y-2 p-4">
                {arch.consumes.map((t: any) => (
                  <div key={t.id} className="flex items-center gap-2 rounded-lg border border-white/5 px-3 py-2">
                    <Activity size={13} className="text-cyan-300" />
                    <span className="mono text-xs text-slate-200">{t.name}</span>
                  </div>
                ))}
                {arch.consumes.length === 0 && <p className="text-xs text-slate-500">No consumed topics.</p>}
              </div>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="deps">
          <div className="grid gap-4 lg:grid-cols-2">
            <DependencyList title="Depends on" items={arch.dependencies} workspaceId={workspaceId} />
            <DependencyList title="Depended on by" items={arch.dependents} workspaceId={workspaceId} />
          </div>
        </TabsContent>

        <TabsContent value="code">
          <Card>
            <CardHeader title="Source navigation" subtitle="Classes indexed for this repository — click to open the exact revision"
              icon={<GitBranch size={15} />} />
            <div className="grid gap-2 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {arch.classes.map((c: any) => (
                <button key={c.id} onClick={() => setSelectedNode(c.id)}
                  className="flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.02] px-3 py-2 text-left hover:border-accent/40">
                  <div className="min-w-0">
                    <div className="truncate text-xs text-slate-200">{c.name}</div>
                    <div className="truncate text-[10px] text-slate-500">{c.file_path}</div>
                  </div>
                  <span className="mono text-[10px] text-slate-500">:{c.start_line}</span>
                </button>
              ))}
              {arch.classes.length === 0 && <p className="text-xs text-slate-500">No classes indexed.</p>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="docs">
          <Card>
            <CardHeader title="Linked documentation" icon={<BookOpen size={15} />} />
            <div className="divide-y divide-white/5">
              {arch.documents.map((d: any) => (
                <Link key={d.id} to={`/w/${workspaceId}/docs?doc=${d.id}`} className="flex items-center gap-3 px-5 py-3 hover:bg-white/[0.03]">
                  <Badge tone="SYNCED">{d.source_type}</Badge>
                  <span className="truncate text-sm text-slate-200">{d.title}</span>
                  <span className="ml-auto text-[10px] text-slate-500">{Math.round(d.confidence * 100)}% confidence</span>
                </Link>
              ))}
              {arch.documents.length === 0 && <div className="p-5"><Empty title="No linked documents" hint="Ingest Markdown, ADRs, OpenAPI or Jira on the Documentation page." /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="ai">
          <Card>
            <CardHeader title="AI insights" subtitle="Stored separately from scanner facts; requires review before approval" icon={<Sparkles size={15} />} />
            <div className="divide-y divide-white/5">
              {arch.ai_insights.map((i: any) => (
                <div key={i.id} className="px-5 py-3">
                  <div className="flex items-center gap-2">
                    <Badge>{i.status}</Badge>
                    <span className="text-[11px] text-slate-400">{i.category}</span>
                    <span className="ml-auto text-[10px] text-slate-500">confidence {Math.round((i.confidence || 0) * 100)}%</span>
                  </div>
                  <p className="mt-1 text-xs text-slate-300">{i.summary}</p>
                </div>
              ))}
              {arch.ai_insights.length === 0 && (
                <div className="p-5">
                  <Empty title="No AI enrichment for this service" hint="Select it in the AI Intelligence Center to generate an overview, responsibilities or risks."
                    action={<Link to={`/w/${workspaceId}/ai?target=${service.id}`} className="text-xs text-accent-soft hover:underline">Open AI Intelligence Center</Link>} />
                </div>
              )}
            </div>
          </Card>
        </TabsContent>
      </Tabs>

      {selectedNode && <SourceViewer nodeId={selectedNode} onClose={() => setSelectedNode(null)} />}
    </div>
  );
}

function DependencyList({ title, items, workspaceId }: { title: string; items: any[]; workspaceId: string }) {
  return (
    <Card>
      <CardHeader title={title} icon={<Workflow size={15} />} />
      <div className="divide-y divide-white/5">
        {items.map((d) => (
          <Link key={`${d.id}-${d.edge?.id || ""}`} to={`/w/${workspaceId}/services/${d.id}`} className="flex items-center gap-2 px-5 py-3 hover:bg-white/[0.03]">
            <Boxes size={13} className="text-accent-soft" />
            <span className="text-xs text-slate-200">{d.name}</span>
            <Badge className="ml-auto">{d.properties?.framework || "service"}</Badge>
          </Link>
        ))}
        {items.length === 0 && <p className="p-5 text-xs text-slate-500">None.</p>}
      </div>
    </Card>
  );
}
