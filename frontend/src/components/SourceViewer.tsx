import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileCode2, GitCommitHorizontal, X } from "lucide-react";
import { Badge, Button, Modal, Spinner, fmt } from "./ui";
import { get } from "../lib/api";

export function SourceViewer({ nodeId, onClose }: { nodeId: string; onClose: () => void }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["source", nodeId],
    queryFn: () => get<any>(`/nodes/${nodeId}/source`),
    retry: false,
  });

  return (
    <Modal open onOpenChange={(v) => !v && onClose()} title={data?.node?.qualified_name || "Source"} width="max-w-4xl"
      description={data ? `${data.repository?.name} · ${data.path} · ${fmt.commit(data.revision)}` : undefined}
      footer={
        <>
          {data?.external_url && (
            <a href={data.external_url} target="_blank" rel="noreferrer">
              <Button variant="outline"><ExternalLink size={14} /> Open in provider</Button>
            </a>
          )}
          <Button variant="ghost" onClick={onClose}>Close</Button>
        </>
      }>
      {isLoading && <Spinner label="Reading indexed revision…" />}
      {error && <p className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">{(error as Error).message}</p>}
      {data && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
            <Badge>{data.node.type}</Badge>
            <span className="inline-flex items-center gap-1"><GitCommitHorizontal size={12} />{fmt.commit(data.revision)}</span>
            <span className="inline-flex items-center gap-1"><FileCode2 size={12} />{data.path}</span>
            <span className={data.verified ? "text-emerald-300" : "text-amber-300"}>
              {data.verified ? "verified at indexed revision" : "revision mismatch"}
            </span>
          </div>
          <div className="max-h-[52vh] overflow-auto rounded-lg border border-white/10 bg-ink-950">
            <pre className="min-w-full text-[12px] leading-5">
              {data.lines.map((line: any) => {
                const highlighted = data.highlight?.start && line.number >= data.highlight.start && line.number <= (data.highlight.end || data.highlight.start);
                return (
                  <div key={line.number} className={`flex ${highlighted ? "bg-accent/10" : ""}`}>
                    <span className="w-12 shrink-0 select-none border-r border-white/5 px-2 text-right text-slate-600">{line.number}</span>
                    <code className="whitespace-pre px-3 font-mono text-slate-300">{line.text || " "}</code>
                  </div>
                );
              })}
            </pre>
          </div>
          {data.history?.length > 0 && (
            <div>
              <div className="label mb-1">Recent commits touching this file</div>
              <ul className="space-y-1 text-[11px] text-slate-400">
                {data.history.map((c: any) => (
                  <li key={c.sha} className="flex gap-2">
                    <span className="mono text-slate-500">{fmt.commit(c.sha)}</span>
                    <span className="truncate">{c.message}</span>
                    <span className="ml-auto shrink-0 text-slate-600">{c.author}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
