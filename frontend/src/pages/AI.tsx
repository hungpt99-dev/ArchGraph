import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Check, ChevronRight, Cpu, FlaskConical, RefreshCw, Sparkles, ThumbsDown,
  ThumbsUp, Zap,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Badge, Button, Card, CardHeader, Empty, Field, Input, KeyValue, Modal, Progress, Select,
  Spinner, Tabs, TabsContent, TabsList, TabsTrigger, cn, fmt, useToast,
} from "../components/ui";
import { get, patch, post } from "../lib/api";

const STATUS_FILTERS = ["", "NEEDS_REVIEW", "GENERATED", "APPROVED", "REJECTED", "STALE", "FAILED", "QUEUED"];

export function AI() {
  const { workspaceId = "" } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [selectedCategories, setSelectedCategories] = useState<string[]>(["SERVICE_OVERVIEW"]);
  const [estimate, setEstimate] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [statusFilter, setStatusFilter] = useState("");
  const [review, setReview] = useState<string | null>(null);
  const [providerModal, setProviderModal] = useState(false);
  const [form, setForm] = useState({ name: "Local mock provider", kind: "mock", base_url: "", model: "archgraph-mock-v1", api_key: "" });

  const status = useQuery({ queryKey: ["ai-status", workspaceId], queryFn: () => get<any>(`/workspaces/${workspaceId}/ai/status`) });
  const services = useQuery({ queryKey: ["ai-services", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/services`) });
  const enrichments = useQuery({
    queryKey: ["enrichments", workspaceId, statusFilter],
    queryFn: () => get<any[]>(`/workspaces/${workspaceId}/ai/enrichments${statusFilter ? `?status=${statusFilter}` : ""}`),
    refetchInterval: 6000,
  });
  const detail = useQuery({
    queryKey: ["enrichment", review],
    queryFn: () => get<any>(`/ai/enrichments/${review}`),
    enabled: Boolean(review),
  });

  const categories = status.data?.categories || [];
  const mock = status.data?.provider_kind === "mock";

  const runEstimate = async () => {
    if (!selectedTargets.length) return toast.push("Select at least one service", "error");
    setBusy(true);
    try {
      const out = await post<any>(`/workspaces/${workspaceId}/ai/estimate`, {
        targets: selectedTargets.map((id) => ({ type: "node", id })), categories: selectedCategories,
      });
      setEstimate(out);
      toast.push(`${out.estimated_total_tokens.toLocaleString()} tokens estimated`, "info");
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const generate = async () => {
    if (!selectedTargets.length || !selectedCategories.length) return toast.push("Select targets and categories", "error");
    setBusy(true);
    try {
      const out = await post<any>(`/workspaces/${workspaceId}/ai/enrich`, {
        targets: selectedTargets.map((id) => ({ type: "node", id })), categories: selectedCategories,
      });
      toast.push(`${out.queued} enrichment job(s) queued`, "success");
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const createProvider = async () => {
    setBusy(true);
    try {
      await post(`/workspaces/${workspaceId}/ai/providers`, { ...form, api_key: form.api_key || null, is_default: true });
      toast.push("Provider saved", "success");
      setProviderModal(false);
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const testProvider = async (id: string) => {
    try {
      const out = await post<any>(`/ai/providers/${id}/test`);
      toast.push(out.ok ? "Provider responded" : out.error, out.ok ? "success" : "error");
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  const reviewAction = async (id: string, action: "approve" | "reject") => {
    try {
      await post(`/ai/enrichments/${id}/${action}`, { note: "" });
      toast.push(`Insight ${action}d`, "success");
      setReview(null);
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  const pending = useMemo(() => enrichments.data?.filter((e) => ["NEEDS_REVIEW", "GENERATED"].includes(e.status)) || [], [enrichments.data]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-white">AI Intelligence Center</h1>
            {status.data?.ai_enabled ? <Badge tone="APPROVED">enabled</Badge> : <Badge tone="PENDING">disabled</Badge>}
            {mock && <Badge tone="MOCK">mock provider</Badge>}
          </div>
          <p className="mt-1 max-w-2xl text-xs text-slate-400">
            AI is an optional enrichment layer. Pick exactly which services and categories to analyse;
            nothing is sent automatically. Results are stored separately from scanner facts and require review.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setProviderModal(true)}><Cpu size={14} /> Provider</Button>
          <Button variant="primary" onClick={generate} loading={busy} disabled={!status.data?.ai_enabled}>
            <Zap size={14} /> Generate
          </Button>
        </div>
      </div>

      {mock && (
        <div className="flex items-start gap-2 rounded-lg border border-fuchsia-500/25 bg-fuchsia-500/5 px-4 py-3 text-[11px] text-fuchsia-200">
          <FlaskConical size={14} className="mt-0.5" />
          The active provider is the <strong>offline deterministic mock</strong>: it produces evidence-summary text locally
          and is labelled MOCK on every record. Configure OpenAI, Anthropic or Ollama for real analysis.
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {["APPROVED", "NEEDS_REVIEW", "STALE", "FAILED"].map((key) => (
          <Card key={key} className="p-4">
            <div className="label">{key.replace("_", " ")}</div>
            <div className="mt-1 text-2xl font-semibold text-slate-100">{status.data?.usage?.by_status?.[key]?.count || 0}</div>
          </Card>
        ))}
      </div>

      <Tabs defaultValue="enrich">
        <TabsList>
          <TabsTrigger value="enrich">Selective enrichment</TabsTrigger>
          <TabsTrigger value="review">Review queue ({pending.length})</TabsTrigger>
          <TabsTrigger value="providers">Providers</TabsTrigger>
          <TabsTrigger value="usage">Usage</TabsTrigger>
        </TabsList>

        <TabsContent value="enrich">
          <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
            <Card>
              <CardHeader title="1 · Choose targets" subtitle={`${selectedTargets.length} selected`} icon={<Sparkles size={15} />}
                actions={<button className="text-[11px] text-slate-400 hover:text-white" onClick={() => setSelectedTargets(selectedTargets.length === (services.data?.length || 0) ? [] : (services.data || []).map((s) => s.id))}>
                  {selectedTargets.length === (services.data?.length || 0) ? "Clear all" : "Select all"}
                </button>} />
              <div className="max-h-80 divide-y divide-white/5 overflow-auto">
                {services.data?.map((s) => (
                  <label key={s.id} className="flex cursor-pointer items-center gap-3 px-5 py-2.5 hover:bg-white/[0.03]">
                    <input type="checkbox" checked={selectedTargets.includes(s.id)}
                      onChange={(e) => setSelectedTargets(e.target.checked ? [...selectedTargets, s.id] : selectedTargets.filter((id) => id !== s.id))}
                      className="h-3.5 w-3.5 accent-indigo-500" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-xs text-slate-200">{s.name}</div>
                      <div className="text-[10px] text-slate-500">{s.api_count} APIs · {s.repository_name}</div>
                    </div>
                    <Badge>{s.language}</Badge>
                  </label>
                ))}
                {services.data?.length === 0 && <p className="p-5 text-xs text-slate-500">No services to enrich yet.</p>}
              </div>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader title="2 · Choose what to generate" subtitle="Only these categories consume tokens" icon={<Zap size={15} />} />
                <div className="grid max-h-64 gap-1.5 overflow-auto p-4 sm:grid-cols-2">
                  {categories.map((c: any) => (
                    <label key={c.key} className={cn("flex cursor-pointer items-start gap-2 rounded-lg border px-2.5 py-2 text-[11px]",
                      selectedCategories.includes(c.key) ? "border-accent/50 bg-accent/10 text-slate-100" : "border-white/10 text-slate-400 hover:border-white/20")}>
                      <input type="checkbox" checked={selectedCategories.includes(c.key)}
                        onChange={(e) => setSelectedCategories(e.target.checked ? [...selectedCategories, c.key] : selectedCategories.filter((k) => k !== c.key))}
                        className="mt-0.5 h-3 w-3 accent-indigo-500" />
                      <span>{c.label}</span>
                    </label>
                  ))}
                </div>
              </Card>

              <Card>
                <CardHeader title="3 · Preview & run" subtitle="Estimate before spending tokens" icon={<RefreshCw size={15} />} />
                <div className="space-y-3 p-4">
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="secondary" onClick={runEstimate} loading={busy}>Estimate</Button>
                    <Button size="sm" variant="primary" onClick={generate} loading={busy} disabled={!status.data?.ai_enabled}>
                      Queue enrichment
                    </Button>
                  </div>
                  {estimate && (
                    <div className="space-y-2 rounded-lg border border-white/5 bg-white/[0.02] p-3">
                      <div className="grid grid-cols-3 gap-2 text-center text-[10px]">
                        <div><div className="text-slate-500">input</div><div className="text-slate-200">{estimate.estimated_input_tokens.toLocaleString()}</div></div>
                        <div><div className="text-slate-500">output</div><div className="text-slate-200">{estimate.estimated_output_tokens.toLocaleString()}</div></div>
                        <div><div className="text-slate-500">provider</div><div className="text-fuchsia-300">{estimate.provider_kind || "none"}</div></div>
                      </div>
                      <Progress value={Math.min(100, estimate.estimated_total_tokens / 500)} />
                      <p className="text-[10px] text-slate-500">{estimate.note}</p>
                    </div>
                  )}
                  {!status.data?.ai_enabled && (
                    <p className="flex items-center gap-1.5 text-[11px] text-amber-300"><AlertTriangle size={12} /> Configure an enabled provider to generate.</p>
                  )}
                </div>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="review">
          <div className="mb-3 flex flex-wrap gap-1.5">
            {STATUS_FILTERS.map((s) => (
              <button key={s || "all"} onClick={() => setStatusFilter(s)}
                className={cn("rounded-md border px-2 py-1 text-[10px] uppercase tracking-wide",
                  statusFilter === s ? "border-accent/50 bg-accent/15 text-white" : "border-white/10 text-slate-500 hover:text-slate-300")}>
                {s || "all"}
              </button>
            ))}
          </div>
          <Card>
            <div className="divide-y divide-white/5">
              {enrichments.data?.map((e) => (
                <button key={e.id} onClick={() => setReview(e.id)} className="flex w-full items-start gap-3 px-5 py-3 text-left hover:bg-white/[0.03]">
                  <Badge tone={e.status}>{e.status.replace("_", " ")}</Badge>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-slate-200">{e.target_name}</span>
                      <span className="text-[10px] text-slate-500">{e.category}</span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[11px] text-slate-400">{e.summary || "—"}</p>
                  </div>
                  <div className="shrink-0 text-right text-[10px] text-slate-500">
                    <div>{e.provider} · {Math.round(e.confidence * 100)}%</div>
                    <div>{fmt.ago(e.updated_at)}</div>
                  </div>
                  <ChevronRight size={13} className="mt-1 text-slate-600" />
                </button>
              ))}
              {enrichments.data?.length === 0 && <div className="p-6"><Empty title="Nothing in this state" hint="Select services above and queue an enrichment." /></div>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="providers">
          <div className="grid gap-3 lg:grid-cols-2">
            {status.data?.providers?.map((p: any) => (
              <Card key={p.id} className="p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-slate-100">{p.name}</span>
                      <Badge tone={p.kind === "mock" ? "MOCK" : p.enabled ? "ACTIVE" : "PENDING"}>{p.kind}</Badge>
                      {p.is_default && <Badge tone="SYNCED">default</Badge>}
                    </div>
                    <div className="mono mt-1 text-[10px] text-slate-500">{p.model || "no model"} · {p.base_url || "local"}</div>
                  </div>
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" onClick={() => testProvider(p.id)}>Test</Button>
                  </div>
                </div>
                <div className="mt-3 flex items-center gap-3 text-[10px] text-slate-500">
                  <span>key {p.api_key_configured ? "configured" : "not required"}</span>
                  <span>last: {p.last_status || "never tested"}</span>
                </div>
              </Card>
            ))}
            {status.data?.providers?.length === 0 && <Empty title="No provider configured" action={<Button onClick={() => setProviderModal(true)}>Add provider</Button>} />}
          </div>
        </TabsContent>

        <TabsContent value="usage">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Tokens" subtitle="Recorded per enrichment, including provider and model" />
              <div className="grid grid-cols-2 gap-3 p-4">
                <div className="rounded-lg border border-white/5 p-3"><div className="label">input</div><div className="mt-1 text-xl text-slate-100">{fmt.number(status.data?.usage?.tokens_in)}</div></div>
                <div className="rounded-lg border border-white/5 p-3"><div className="label">output</div><div className="mt-1 text-xl text-slate-100">{fmt.number(status.data?.usage?.tokens_out)}</div></div>
              </div>
            </Card>
            <Card>
              <CardHeader title="Policy" subtitle="Applies to REST and MCP requests" />
              <div className="space-y-2 p-4 text-[11px] text-slate-400">
                <KeyValue items={[
                  ["AI enabled", String(status.data?.policy?.ai_enabled)],
                  ["Require approval", String(status.data?.policy?.require_approval)],
                  ["Allowed categories", status.data?.policy?.allowed_categories?.length ? status.data.policy.allowed_categories.join(", ") : "all"],
                  ["Provider", status.data?.provider?.name || "none"],
                ]} />
              </div>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <Modal open={Boolean(review)} onOpenChange={(v) => !v && setReview(null)} width="max-w-3xl"
        title={detail.data ? `${detail.data.target_name} · ${detail.data.category}` : "Insight"}
        description={detail.data ? `${detail.data.provider}/${detail.data.model} · prompt ${detail.data.prompt_version} · revision ${detail.data.source_revision?.slice(0, 7)}` : undefined}
        footer={detail.data && ["GENERATED", "NEEDS_REVIEW", "STALE"].includes(detail.data.status) ? (
          <>
            <Button variant="danger" onClick={() => reviewAction(detail.data.id, "reject")}><ThumbsDown size={13} /> Reject</Button>
            <Button variant="primary" onClick={() => reviewAction(detail.data.id, "approve")}><ThumbsUp size={13} /> Approve</Button>
          </>
        ) : undefined}>
        {detail.isLoading && <Spinner />}
        {detail.data && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge tone={detail.data.status}>{detail.data.status}</Badge>
              <span className="text-[11px] text-slate-400">confidence {Math.round(detail.data.confidence * 100)}%</span>
              <span className="ml-auto text-[10px] text-slate-500">{detail.data.tokens_in + detail.data.tokens_out} tokens</span>
            </div>
            {detail.data.error && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-2 text-[11px] text-rose-200">{detail.data.error}</p>}
            <p className="text-sm leading-relaxed text-slate-200">{detail.data.content?.summary}</p>
            {detail.data.content?.responsibilities?.length > 0 && (
              <div>
                <div className="label mb-1">Responsibilities</div>
                <ul className="list-disc space-y-1 pl-5 text-[11px] text-slate-300">
                  {detail.data.content.responsibilities.map((r: string, i: number) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            )}
            {detail.data.content?.risks?.length > 0 && (
              <div>
                <div className="label mb-1">Risks</div>
                <ul className="list-disc space-y-1 pl-5 text-[11px] text-amber-200">
                  {detail.data.content.risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            )}
            <div>
              <div className="label mb-1">Evidence referenced</div>
              <div className="space-y-1">
                {detail.data.evidence?.map((e: any, i: number) => (
                  <div key={i} className="mono flex items-center gap-2 rounded border border-white/5 bg-white/[0.02] px-2 py-1 text-[10px] text-slate-400">
                    <Check size={10} className="text-emerald-400" />
                    <span className="truncate">{e.file || e.external_id || e.kind}</span>
                    <span className="ml-auto shrink-0 text-slate-600">{e.commit?.slice(0, 7) || ""}</span>
                  </div>
                ))}
                {detail.data.evidence?.length === 0 && <span className="text-[11px] text-slate-500">No evidence recorded.</span>}
              </div>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={providerModal} onOpenChange={setProviderModal} title="Configure AI provider"
        description="Keys are encrypted at rest and never included in prompts. AI stays optional."
        footer={<><Button variant="ghost" onClick={() => setProviderModal(false)}>Cancel</Button><Button variant="primary" loading={busy} onClick={createProvider}>Save provider</Button></>}>
        <div className="space-y-3">
          <Field label="Display name"><Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Provider kind" hint="openai = any OpenAI-compatible endpoint (vLLM, LM Studio, DeepSeek, OpenAI). ollama runs fully local.">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="openai">OpenAI-compatible</option>
              <option value="anthropic">Anthropic</option>
              <option value="ollama">Ollama (local)</option>
              <option value="mock">Mock (offline demo/testing)</option>
            </Select>
          </Field>
          {form.kind !== "mock" && (
            <>
              <Field label="Base URL" hint="e.g. https://api.openai.com/v1 or http://localhost:11434"><Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} /></Field>
              <Field label="Model"><Input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="gpt-4o-mini" /></Field>
              <Field label="API key" hint="Required for OpenAI/Anthropic; not used by Ollama."><Input type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} /></Field>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
}
