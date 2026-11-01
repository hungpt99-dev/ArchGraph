import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, GitBranch, KeyRound, Plug, Server, Terminal } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, CardHeader, Field, Input, Modal, Select, Spinner, Tabs, TabsContent, TabsList, TabsTrigger, fmt, useToast } from "../components/ui";
import { get, post } from "../lib/api";

export function Integrations() {
  const { workspaceId = "" } = useParams();
  const toast = useToast();
  const qc = useQueryClient();
  const [secret, setSecret] = useState<{ repo: string; data: any } | null>(null);
  const [jiraOpen, setJiraOpen] = useState(false);
  const [form, setForm] = useState({ kind: "jira", base_url: "", email: "", project: "HOTEL", space: "", token: "" });
  const [busy, setBusy] = useState(false);

  const repos = useQuery({ queryKey: ["repos", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/repositories`) });
  const integrations = useQuery({ queryKey: ["integrations", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/integrations`) });
  const tools = useQuery({ queryKey: ["mcp-tools"], queryFn: () => get<any>("/mcp/tools") });
  const config = useQuery({ queryKey: ["mcp-config"], queryFn: () => get<any>("/mcp/config") });

  const rotate = async (repo: any) => {
    try {
      const out = await post<any>(`/repositories/${repo.id}/webhook-secret`);
      setSecret({ repo: repo.name, data: out });
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  const saveIntegration = async () => {
    setBusy(true);
    try {
      await post(`/workspaces/${workspaceId}/integrations`, {
        kind: form.kind,
        name: `${form.kind} · ${form.base_url || form.project || form.space}`,
        config: { base_url: form.base_url, email: form.email, project: form.project, space: form.space },
        secret: form.token,
      });
      toast.push("Integration saved — test connectivity next", "success");
      setJiraOpen(false);
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const testIntegration = async (id: string) => {
    try {
      const out = await post<any>(`/workspaces/${workspaceId}/integrations/${id}/test`);
      toast.push(out.ok ? "Connection OK" : out.error || "Connection failed", out.ok ? "success" : "error");
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-white">Integrations</h1>
        <p className="mt-1 text-xs text-slate-400">Git providers, Jira, Confluence, OpenTelemetry and MCP clients.</p>
      </div>

      <Tabs defaultValue="git">
        <TabsList>
          <TabsTrigger value="git">Git webhooks</TabsTrigger>
          <TabsTrigger value="docs">Jira & Confluence</TabsTrigger>
          <TabsTrigger value="mcp">MCP</TabsTrigger>
        </TabsList>

        <TabsContent value="git">
          <Card>
            <CardHeader title="Repository webhooks" subtitle="Cryptographically verified, idempotent, branch-scoped" icon={<GitBranch size={15} />} />
            <div className="divide-y divide-white/5">
              {repos.data?.map((repo) => (
                <div key={repo.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-200">{repo.name}</span>
                      <Badge tone={repo.provider === "github" ? "SYNCED" : repo.provider === "gitlab" ? "WARN" : "PENDING"}>{repo.provider}</Badge>
                      {repo.webhook_configured && <Badge tone="ACTIVE">webhook</Badge>}
                    </div>
                    <div className="mono mt-0.5 truncate text-[10px] text-slate-500">
                      POST /api/webhooks/{repo.provider === "gitlab" ? "gitlab" : "github"}/{repo.id}
                    </div>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => rotate(repo)}><KeyRound size={12} /> {repo.webhook_configured ? "Rotate secret" : "Create secret"}</Button>
                </div>
              ))}
              {repos.data?.length === 0 && <p className="p-5 text-xs text-slate-500">Add a repository first.</p>}
            </div>
          </Card>
          <p className="mt-2 text-[11px] text-slate-500">
            GitHub sends <span className="mono">X-Hub-Signature-256</span> (HMAC-SHA256 of the raw body); GitLab sends a secret token.
            Replays are rejected by delivery id, and the payload never supplies source code — the scanner re-fetches the commit.
          </p>
        </TabsContent>

        <TabsContent value="docs">
          <div className="mb-3 flex justify-end">
            <Button variant="primary" onClick={() => setJiraOpen(true)}><Plug size={14} /> Add integration</Button>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {integrations.data?.map((i) => (
              <Card key={i.id} className="p-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-slate-100">{i.name}</span>
                      <Badge tone={i.status === "CONNECTED" ? "ACTIVE" : i.status === "ERROR" ? "FAILED" : "PENDING"}>{i.status}</Badge>
                    </div>
                    <div className="mono mt-1 text-[10px] text-slate-500">{i.config?.base_url} {i.config?.project || i.config?.space}</div>
                    <div className="mt-1 text-[10px] text-slate-500">secret {i.secret_configured ? "stored (encrypted)" : "missing"} · last sync {fmt.ago(i.last_sync_at)}</div>
                  </div>
                  <Button size="sm" variant="outline" onClick={() => testIntegration(i.id)}>Test</Button>
                </div>
                {i.last_error && <p className="mt-2 text-[10px] text-rose-300">{i.last_error}</p>}
              </Card>
            ))}
            {integrations.data?.length === 0 && (
              <Card className="p-6 text-xs text-slate-500">
                No Jira or Confluence integration yet. Live sync needs real credentials; without them the demo uses
                the Jira-shaped requirement fixtures ingested at seed time.
              </Card>
            )}
          </div>
        </TabsContent>

        <TabsContent value="mcp">
          <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
            <Card>
              <CardHeader title="MCP server" subtitle={`${tools.data?.count || 0} structured architecture tools`} icon={<Terminal size={15} />} />
              <div className="max-h-96 overflow-auto p-4">
                <div className="grid gap-1.5 sm:grid-cols-2">
                  {tools.data?.tools?.map((t: any) => (
                    <div key={t.name} className="rounded border border-white/5 bg-white/[0.02] px-2 py-1">
                      <div className="mono text-[10px] text-slate-200">{t.name}</div>
                      <div className="line-clamp-1 text-[9px] text-slate-500">{t.description}</div>
                    </div>
                  ))}
                </div>
              </div>
            </Card>
            <Card>
              <CardHeader title="Connect a client" icon={<Server size={15} />} />
              <div className="space-y-3 p-4 text-[11px] text-slate-400">
                <p>Create an API key in Settings, scoped to this workspace, then:</p>
                <pre className="overflow-auto rounded-lg border border-white/10 bg-ink-950 p-3 text-[10px] text-slate-300">{`claude mcp add archgraph \\
  -- docker compose exec -T \\
  -e ARCHGRAPH_API_KEY=<key> api \\
  python -m app.mcp_server`}</pre>
                <pre className="overflow-auto rounded-lg border border-white/10 bg-ink-950 p-3 text-[10px] text-slate-300">{`# streamable HTTP
URL  ${config.data?.streamable_http?.url || "http://localhost:8080/mcp"}
Auth Bearer <key>`}</pre>
                <p className="text-slate-500">Every call is audited with the key owner, tool name and arguments. Unauthenticated requests receive 401.</p>
              </div>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <Modal open={Boolean(secret)} onOpenChange={(v) => !v && setSecret(null)} title={`Webhook secret · ${secret?.repo || ""}`}
        description="Shown once. Paste it into the repository's webhook configuration."
        footer={<Button variant="primary" onClick={() => setSecret(null)}>Done</Button>}>
        {secret && (
          <div className="space-y-3 text-xs">
            <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-amber-200">{secret.data.warning}</div>
            <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-ink-950 p-3">
              <code className="mono flex-1 break-all text-[11px] text-slate-200">{secret.data.secret}</code>
              <button onClick={() => { navigator.clipboard?.writeText(secret.data.secret); toast.push("Copied", "success"); }}><Copy size={14} /></button>
            </div>
            <div className="space-y-1 text-[11px] text-slate-400">
              <div>GitHub: <span className="mono">{secret.data.github_webhook_url}</span></div>
              <div>GitLab: <span className="mono">{secret.data.gitlab_webhook_url}</span></div>
              <div>Events: {secret.data.events.join(", ")}</div>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={jiraOpen} onOpenChange={setJiraOpen} title="Add documentation integration"
        description="Credentials are encrypted at rest. Connectivity is probed without importing data."
        footer={<><Button variant="ghost" onClick={() => setJiraOpen(false)}>Cancel</Button><Button variant="primary" loading={busy} onClick={saveIntegration} disabled={!form.base_url || !form.token}>Save</Button></>}>
        <div className="space-y-3">
          <Field label="Kind">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="jira">Jira Cloud</option><option value="confluence">Confluence Cloud</option>
            </Select>
          </Field>
          <Field label="Base URL" required><Input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://acme.atlassian.net" /></Field>
          <Field label="Email"><Input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="bot@acme.com" /></Field>
          {form.kind === "jira"
            ? <Field label="Project key"><Input value={form.project} onChange={(e) => setForm({ ...form, project: e.target.value })} /></Field>
            : <Field label="Space key"><Input value={form.space} onChange={(e) => setForm({ ...form, space: e.target.value })} /></Field>}
          <Field label="API token" required hint="Atlassian API token (basic auth) or an OAuth bearer token.">
            <Input type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
