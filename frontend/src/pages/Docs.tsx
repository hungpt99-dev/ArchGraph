import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileUp, Filter, Link2, RefreshCw, Search } from "lucide-react";
import { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, CardHeader, Empty, Input, Modal, Select, Spinner, cn, fmt, useToast } from "../components/ui";
import { api, get, post } from "../lib/api";

const SOURCES = ["", "markdown", "adr", "openapi", "asyncapi", "jira", "confluence", "pdf", "docx"];

export function Docs() {
  const { workspaceId = "" } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [source, setSource] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const docs = useQuery({
    queryKey: ["documents", workspaceId, q, source],
    queryFn: () => get<any[]>(`/workspaces/${workspaceId}/documents?${new URLSearchParams({ ...(q ? { q } : {}), ...(source ? { source_type: source } : {}), limit: "200" })}`),
  });
  const stats = useQuery({ queryKey: ["doc-stats", workspaceId], queryFn: () => get<any>(`/workspaces/${workspaceId}/documents/stats`) });
  const detail = useQuery({
    queryKey: ["document", selected],
    queryFn: () => get<any>(`/documents/${selected}`),
    enabled: Boolean(selected),
  });

  const sync = async () => {
    setSyncing(true);
    try {
      await post(`/workspaces/${workspaceId}/documents/sync`, { targets: ["repositories"] });
      toast.push("Documentation synced from indexed repositories", "success");
      await qc.invalidateQueries();
    } catch (error: any) { toast.push(error.message, "error"); }
    finally { setSyncing(false); }
  };

  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    try {
      await api(`/workspaces/${workspaceId}/documents/upload`, { method: "POST", body: form });
      toast.push("Document ingested", "success");
      await qc.invalidateQueries();
    } catch (error: any) { toast.push(error.message, "error"); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Documentation</h1>
          <p className="mt-1 text-xs text-slate-400">
            Markdown, ADRs, OpenAPI/AsyncAPI, Jira and Confluence pages, linked to the architecture by deterministic name matching.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".md,.pdf,.docx,.txt" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
          <Button variant="outline" onClick={() => fileRef.current?.click()}><FileUp size={14} /> Upload</Button>
          <Button variant="primary" onClick={sync} loading={syncing}><RefreshCw size={14} /> Sync from repos</Button>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {Object.entries(stats.data?.by_source || {}).map(([key, value]) => (
          <Card key={key} className="p-3">
            <div className="label">{key}</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">{value as number}</div>
          </Card>
        ))}
        <Card className="p-3">
          <div className="label">links to code</div>
          <div className="mt-1 text-lg font-semibold text-accent-soft">{stats.data?.links ?? 0}</div>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search documents…" className="pl-9" />
        </div>
        <Select value={source} onChange={(e) => setSource(e.target.value)} className="w-44">
          {SOURCES.map((s) => <option key={s} value={s}>{s ? s : "All sources"}</option>)}
        </Select>
        <span className="ml-auto text-[11px] text-slate-500">{docs.data?.length || 0} documents</span>
      </div>

      {docs.isLoading ? <Spinner /> : docs.data?.length === 0 ? (
        <Empty title="No documents" hint="Sync from indexed repositories or upload a PDF/DOCX/Markdown file." />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {docs.data?.map((doc) => (
            <Card key={doc.id} className="cursor-pointer p-4 transition hover:border-accent/40" onClick={() => setSelected(doc.id)}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Badge tone={doc.source_type === "jira" ? "WARN" : doc.source_type === "adr" ? "AI_INFERRED" : "SYNCED"}>{doc.source_type}</Badge>
                    <span className="truncate text-sm font-medium text-slate-100">{doc.title}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-slate-500">{doc.excerpt}</p>
                </div>
              </div>
              <div className="mt-3 flex items-center gap-3 text-[10px] text-slate-500">
                <span className="mono">{doc.external_id?.slice(-40)}</span>
                <span className="ml-auto">{fmt.ago(doc.updated_at)}</span>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal open={Boolean(selected)} onOpenChange={(v) => !v && setSelected(null)}
        title={detail.data?.document?.title || "Document"} width="max-w-3xl"
        description={detail.data ? `${detail.data.document.source_type} · ${detail.data.document.external_id}` : undefined}
        footer={detail.data?.document?.url ? <a href={detail.data.document.url} target="_blank" rel="noreferrer" className="text-xs text-accent-soft hover:underline">Open source</a> : undefined}>
        {detail.isLoading && <Spinner />}
        {detail.data && (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2 text-[10px] text-slate-500">
              <Badge>{detail.data.document.status}</Badge>
              <span>version {detail.data.document.version || "—"}</span>
              <span>hash {detail.data.document.content_hash?.slice(0, 10)}</span>
            </div>
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-ink-950 p-3 text-[11px] leading-relaxed text-slate-300">
              {detail.data.document.content.slice(0, 8000)}
            </pre>
            <div>
              <div className="label mb-2 flex items-center gap-1.5"><Link2 size={12} /> Linked architecture ({detail.data.links.length})</div>
              <div className="flex flex-wrap gap-1.5">
                {detail.data.links.map((l: any) => (
                  <span key={l.id} className={cn("rounded-md border px-2 py-1 text-[10px]",
                    l.approved ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200" : "border-amber-500/25 bg-amber-500/10 text-amber-200")}>
                    {l.target_node_type} · {l.target_name} <span className="text-slate-500">({l.detection_method}, {Math.round(l.confidence * 100)}%)</span>
                  </span>
                ))}
                {detail.data.links.length === 0 && <span className="text-[11px] text-slate-500">No links detected yet.</span>}
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
