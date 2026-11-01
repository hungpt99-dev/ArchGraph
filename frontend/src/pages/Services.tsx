import { useQuery } from "@tanstack/react-query";
import { Boxes, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, Empty, Input, Spinner, fmt } from "../components/ui";
import { get } from "../lib/api";

export function Services() {
  const { workspaceId = "" } = useParams();
  const [query, setQuery] = useState("");
  const { data, isLoading } = useQuery({
    queryKey: ["services", workspaceId],
    queryFn: () => get<any[]>(`/workspaces/${workspaceId}/services`),
  });

  const services = useMemo(
    () => (data || []).filter((s) => !query || s.name.toLowerCase().includes(query.toLowerCase())),
    [data, query],
  );

  if (isLoading) return <Spinner label="Loading services…" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Services</h1>
          <p className="mt-1 text-sm text-slate-400">
            {data?.length || 0} services discovered. One repository can host several services; each service maps to one or more modules.
          </p>
        </div>
        <div className="relative w-72">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter services…" className="pl-9" />
        </div>
      </div>

      {services.length === 0 ? (
        <Empty title="No services match" hint="Add a repository or run a scan from the Synchronization page." />
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {services.map((s) => (
            <Link key={s.id} to={`/w/${workspaceId}/services/${s.id}`}>
              <Card className="p-4 transition hover:border-accent/40 hover:shadow-glow">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-accent/10 text-accent-soft"><Boxes size={16} /></div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium text-white">{s.name}</div>
                      <div className="truncate text-[11px] text-slate-500">{s.qualified_name}</div>
                    </div>
                  </div>
                  <Badge tone={s.language === "java" ? "QUEUED" : "GENERATED"}>{s.language || "n/a"}</Badge>
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[11px]">
                  <div className="rounded-md border border-white/5 bg-white/[0.02] py-1.5"><div className="text-slate-200">{s.api_count}</div><div className="text-slate-500">endpoints</div></div>
                  <div className="rounded-md border border-white/5 bg-white/[0.02] py-1.5"><div className="text-slate-200">{s.dependency_count}</div><div className="text-slate-500">deps</div></div>
                  <div className="rounded-md border border-white/5 bg-white/[0.02] py-1.5"><div className="text-slate-200">{s.port ?? "—"}</div><div className="text-slate-500">port</div></div>
                </div>
                <div className="mt-3 flex items-center justify-between text-[10px] text-slate-500">
                  <span>{s.repository_name}</span>
                  <span className="mono">seen {fmt.ago(s.last_seen)}</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
