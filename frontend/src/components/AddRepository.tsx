import { useQueryClient } from "@tanstack/react-query";
import { GitBranch, Plus } from "lucide-react";
import { useState } from "react";
import { Button, Field, Input, Modal, Select, useToast } from "./ui";
import { post } from "../lib/api";

export function AddRepositoryModal({ workspaceId, open, onOpenChange, onAdded }: {
  workspaceId: string; open: boolean; onOpenChange: (v: boolean) => void; onAdded?: () => void;
}) {
  const toast = useToast();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [credential, setCredential] = useState("");
  const [provider, setProvider] = useState("auto");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await post(`/workspaces/${workspaceId}/repositories`, {
        name, url, branch, provider, credential: credential || null, scan_now: true,
      });
      toast.push("Repository added — initial scan queued", "success");
      await qc.invalidateQueries();
      onAdded?.();
      onOpenChange(false);
      setName(""); setUrl(""); setCredential("");
    } catch (error: any) {
      toast.push(error.message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Add repository"
      description="ArchGraph clones the repository and scans it. Tokens are encrypted at rest and never sent to an LLM."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!name || !url} onClick={submit}>
            <Plus size={14} /> Add & scan
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Repository name" hint="Used to label the repository in the graph." required>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="payment-service" />
        </Field>
        <Field label="Git URL" hint="HTTPS URL, or an absolute path for a local mirror (self-hosted demo)." required>
          <Input value={url} onChange={(e) => { setUrl(e.target.value); }} placeholder="https://github.com/acme/payment-service.git" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Branch"><Input value={branch} onChange={(e) => setBranch(e.target.value)} /></Field>
          <Field label="Provider">
            <Select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="auto">Auto-detect</option>
              <option value="github">GitHub</option>
              <option value="gitlab">GitLab</option>
              <option value="local">Local path</option>
            </Select>
          </Field>
        </div>
        <Field label="Access token (optional)" hint="Stored Fernet-encrypted. Never logged, never used in AI context.">
          <Input type="password" value={credential} onChange={(e) => setCredential(e.target.value)} placeholder="ghp_…" />
        </Field>
        <div className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.02] p-3 text-[11px] text-slate-400">
          <GitBranch size={14} className="mt-0.5 shrink-0 text-accent-soft" />
          A full scan runs immediately. Later pushes can be synced through GitHub/GitLab webhooks
          configured on the Integrations page.
        </div>
      </div>
    </Modal>
  );
}
