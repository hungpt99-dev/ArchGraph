import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, ScrollText, Shield, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { Badge, Button, Card, CardHeader, Field, Input, KeyValue, Modal, Select, Spinner, Tabs, TabsContent, TabsList, TabsTrigger, fmt, useToast } from "../components/ui";
import { del, get, patch, post } from "../lib/api";
import { useAuth } from "../lib/auth";

export function SettingsPage() {
  const { workspaceId = "" } = useParams();
  const { user, refresh } = useAuth();
  const toast = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [keyName, setKeyName] = useState("Claude Code");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const workspace = useQuery({ queryKey: ["workspace", workspaceId], queryFn: () => get<any>(`/workspaces/${workspaceId}`) });
  const members = useQuery({ queryKey: ["members", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/members`) });
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => get<any[]>("/auth/api-keys") });
  const audit = useQuery({ queryKey: ["audit", workspaceId], queryFn: () => get<any[]>(`/workspaces/${workspaceId}/audit?limit=60`) });
  const users = useQuery({ queryKey: ["users"], queryFn: () => get<any[]>("/admin/users"), retry: false });
  const system = useQuery({ queryKey: ["system"], queryFn: () => get<any>("/admin/system"), retry: false });

  if (workspace.isLoading) return <Spinner />;
  if (workspace.data && !initialized) {
    setName(workspace.data.name);
    setDescription(workspace.data.description || "");
    setInitialized(true);
  }

  const saveWorkspace = async () => {
    try {
      await patch(`/workspaces/${workspaceId}`, { name, description });
      toast.push("Workspace updated", "success");
      await refresh();
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  const createKey = async () => {
    setBusy(true);
    try {
      const out = await post<any>("/auth/api-keys", { name: keyName, workspace_id: workspaceId, scopes: ["read", "enrich"] });
      setNewKey(out.api_key);
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
    finally { setBusy(false); }
  };

  const revoke = async (id: string) => {
    try { await del(`/auth/api-keys/${id}`); toast.push("Key revoked", "success"); await qc.invalidateQueries(); }
    catch (e: any) { toast.push(e.message, "error"); }
  };

  const addMember = async (userId: string, role: string) => {
    try {
      await post(`/workspaces/${workspaceId}/members`, { user_id: userId, role });
      toast.push("Member added", "success");
      await qc.invalidateQueries();
    } catch (e: any) { toast.push(e.message, "error"); }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-white">Settings</h1>
        <p className="mt-1 text-xs text-slate-400">Workspace, access control, API keys and audit trail.</p>
      </div>

      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="access">Access</TabsTrigger>
          <TabsTrigger value="keys">API keys</TabsTrigger>
          <TabsTrigger value="audit">Audit</TabsTrigger>
          <TabsTrigger value="system">System</TabsTrigger>
        </TabsList>

        <TabsContent value="general">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Workspace" />
              <div className="space-y-3 p-4">
                <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
                <Field label="Description"><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
                <Field label="Slug"><Input value={workspace.data?.slug} disabled className="opacity-60" /></Field>
                <Button variant="primary" onClick={saveWorkspace}>Save changes</Button>
              </div>
            </Card>
            <Card>
              <CardHeader title="AI policy" subtitle="AI never runs unless explicitly requested" icon={<Shield size={15} />} />
              <div className="space-y-3 p-4">
                <label className="flex items-center justify-between rounded-lg border border-white/5 px-3 py-2 text-xs text-slate-300">
                  Allow AI enrichment in this workspace
                  <input type="checkbox" defaultChecked={workspace.data?.settings?.ai_enabled !== false}
                    onChange={async (e) => { await patch(`/workspaces/${workspaceId}`, { settings: { ...(workspace.data?.settings || {}), ai_enabled: e.target.checked } }); await qc.invalidateQueries(); toast.push("Policy updated", "success"); }} />
                </label>
                <label className="flex items-center justify-between rounded-lg border border-white/5 px-3 py-2 text-xs text-slate-300">
                  Require human approval for AI insights
                  <input type="checkbox" defaultChecked={workspace.data?.settings?.ai_require_approval !== false}
                    onChange={async (e) => { await patch(`/workspaces/${workspaceId}`, { settings: { ...(workspace.data?.settings || {}), ai_require_approval: e.target.checked } }); await qc.invalidateQueries(); }} />
                </label>
                <p className="text-[11px] text-slate-500">Deterministic scanning, graph queries and MCP tools work with AI fully disabled.</p>
              </div>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="access">
          <Card>
            <CardHeader title="Members" subtitle="Roles: SYSTEM_ADMIN, WORKSPACE_ADMIN, ARCHITECT, DEVELOPER, VIEWER" />
            <div className="divide-y divide-white/5">
              {members.data?.map((m: any) => (
                <div key={m.id} className="flex items-center gap-3 px-5 py-3">
                  <div className="grid h-7 w-7 place-items-center rounded-full bg-accent/15 text-[10px] text-accent-soft">{m.name?.slice(0, 1) || "?"}</div>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-slate-200">{m.name}</div>
                    <div className="text-[10px] text-slate-500">{m.email}</div>
                  </div>
                  <Badge>{m.role}</Badge>
                </div>
              ))}
            </div>
            {users.data && users.data.length > 0 && (
              <div className="border-t border-white/5 p-4">
                <div className="label mb-2 flex items-center gap-1.5"><UserPlus size={12} /> Add an existing user</div>
                <div className="flex flex-wrap gap-2">
                  {users.data.filter((u: any) => !members.data?.some((m: any) => m.user_id === u.id)).map((u: any) => (
                    <button key={u.id} onClick={() => addMember(u.id, "DEVELOPER")}
                      className="rounded-md border border-white/10 px-2 py-1 text-[10px] text-slate-300 hover:border-accent/50">
                      {u.name} · {u.email}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="keys">
          <Card>
            <CardHeader title="API keys" subtitle="Used by MCP clients and automation. Scoped to this workspace." icon={<KeyRound size={15} />} />
            <div className="space-y-3 p-4">
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex-1"><Field label="Key name"><Input value={keyName} onChange={(e) => setKeyName(e.target.value)} /></Field></div>
                <Button variant="primary" loading={busy} onClick={createKey}>Create key</Button>
              </div>
              <div className="divide-y divide-white/5 rounded-lg border border-white/5">
                {keys.data?.map((k) => (
                  <div key={k.id} className="flex items-center gap-3 px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="text-xs text-slate-200">{k.name}</div>
                      <div className="mono text-[10px] text-slate-500">{k.prefix}… · created {fmt.ago(k.created_at)} · last used {fmt.ago(k.last_used_at)}</div>
                    </div>
                    <Badge tone={k.revoked_at ? "REJECTED" : "ACTIVE"}>{k.revoked_at ? "revoked" : "active"}</Badge>
                    {!k.revoked_at && <button onClick={() => revoke(k.id)} className="text-slate-500 hover:text-rose-300"><Trash2 size={13} /></button>}
                  </div>
                ))}
                {keys.data?.length === 0 && <p className="p-3 text-[11px] text-slate-500">No keys yet.</p>}
              </div>
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="audit">
          <Card>
            <CardHeader title="Audit trail" subtitle="Every mutation, scan, webhook, AI action and MCP call" icon={<ScrollText size={15} />} />
            <div className="max-h-[520px] divide-y divide-white/5 overflow-auto">
              {audit.data?.map((a) => (
                <div key={a.id} className="flex flex-wrap items-center gap-2 px-5 py-2 text-[11px]">
                  <span className="mono text-[10px] text-slate-500">{fmt.date(a.created_at)}</span>
                  <Badge>{a.actor_type}</Badge>
                  <span className="text-slate-300">{a.actor}</span>
                  <span className="mono text-accent-soft">{a.action}</span>
                  <span className="ml-auto truncate text-[10px] text-slate-500">{a.object_type} {a.object_id?.slice(0, 8)}</span>
                </div>
              ))}
              {audit.data?.length === 0 && <p className="p-5 text-xs text-slate-500">No audit events.</p>}
            </div>
          </Card>
        </TabsContent>

        <TabsContent value="system">
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Runtime" />
              <div className="p-4">
                <KeyValue items={[
                  ["Version", system.data?.version || "1.0.0"],
                  ["Environment", system.data?.environment || "—"],
                  ["Users", system.data?.counts?.users ?? "—"],
                  ["Workspaces", system.data?.counts?.workspaces ?? "—"],
                  ["AI required", "no"],
                  ["Session role", user?.role || "—"],
                ]} />
              </div>
            </Card>
            <Card>
              <CardHeader title="Security posture" />
              <div className="space-y-1.5 p-4 text-[11px] text-slate-400">
                {Object.entries(system.data?.security || {}).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-b border-white/5 py-1 last:border-0">
                    <span className="capitalize">{k.replaceAll("_", " ")}</span>
                    <span className="text-slate-200">{String(v)}</span>
                  </div>
                ))}
                {!system.data && <p className="text-slate-500">System information requires SYSTEM_ADMIN. Your role: {user?.role}</p>}
              </div>
            </Card>
          </div>
        </TabsContent>
      </Tabs>

      <Modal open={Boolean(newKey)} onOpenChange={(v) => !v && setNewKey(null)} title="API key created"
        description="Copy it now — it will not be shown again." footer={<Button variant="primary" onClick={() => setNewKey(null)}>Done</Button>}>
        <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-ink-950 p-3">
          <code className="mono flex-1 break-all text-[11px] text-slate-200">{newKey}</code>
          <button onClick={() => { navigator.clipboard?.writeText(newKey || ""); toast.push("Copied", "success"); }}><Copy size={14} /></button>
        </div>
      </Modal>
    </div>
  );
}
