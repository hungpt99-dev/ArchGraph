import { useQuery } from "@tanstack/react-query";
import cytoscape from "cytoscape";
import {
  Crosshair, Eye, Filter, GitCompareArrows, Layers, Loader2, Maximize2, Route, Search, Sparkles, X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { SourceViewer } from "../components/SourceViewer";
import { Badge, Button, Card, Empty, Input, KeyValue, Select, Spinner, cn, fmt } from "../components/ui";
import { get, post } from "../lib/api";

const NODE_COLORS: Record<string, string> = {
  SERVICE: "#6366f1", KAFKA_TOPIC: "#22d3ee", DATABASE: "#f59e0b", TABLE: "#fbbf24",
  API_ENDPOINT: "#34d399", CLASS: "#a78bfa", INTERFACE: "#c084fc", METHOD: "#94a3b8",
  REDIS_CACHE: "#f472b6", BACKGROUND_JOB: "#fb923c", REPOSITORY: "#64748b", PACKAGE: "#475569",
  MODULE: "#7c3aed", EXTERNAL_SYSTEM: "#ef4444", FUNCTION: "#94a3b8", COLUMN: "#a3a3a3",
};
const NODE_SIZES: Record<string, number> = {
  SERVICE: 52, KAFKA_TOPIC: 38, DATABASE: 40, TABLE: 26, API_ENDPOINT: 24, CLASS: 26, PACKAGE: 20, MODULE: 24,
};
const NODE_FILTERS = ["SERVICE", "API_ENDPOINT", "KAFKA_TOPIC", "DATABASE", "TABLE", "CLASS", "METHOD", "REDIS_CACHE", "BACKGROUND_JOB"];
const EDGE_FILTERS = ["DEPENDS_ON", "ROUTES_TO", "PUBLISHES", "CONSUMES", "READS", "WRITES", "USES_CACHE", "CALLS", "EXPOSES", "SCHEDULES"];

export function Explorer() {
  const { workspaceId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [seeds, setSeeds] = useState<string[]>(() => (params.get("node") ? [params.get("node")!] : []));
  const [depth, setDepth] = useState(1);
  const [nodeTypes, setNodeTypes] = useState<string[]>([]);
  const [edgeTypes, setEdgeTypes] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(params.get("node"));
  const [showSource, setShowSource] = useState(false);
  const [pathFrom, setPathFrom] = useState<string | null>(null);
  const [pathTo, setPathTo] = useState<string | null>(null);
  const [pathResult, setPathResult] = useState<any>(null);
  const [impact, setImpact] = useState<any>(null);
  const [search, setSearch] = useState(params.get("q") || "");
  const [layout, setLayout] = useState("cose");
  const [busy, setBusy] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const graphQuery = new URLSearchParams();
  seeds.forEach((s) => graphQuery.append("node_id", s));
  graphQuery.set("depth", String(depth));
  if (edgeTypes.length) graphQuery.set("edge_types", edgeTypes.join(","));
  if (nodeTypes.length) graphQuery.set("node_types", nodeTypes.join(","));

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["graph", workspaceId, seeds.join(","), depth, edgeTypes.join(","), nodeTypes.join(",")],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/graph?${graphQuery.toString()}`),
  });

  const detail = useQuery({
    queryKey: ["node", workspaceId, selected],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/nodes/${selected}`),
    enabled: Boolean(selected),
  });

  const searchResults = useQuery({
    queryKey: ["graph-search", workspaceId, search],
    queryFn: () => get<any>(`/workspaces/${workspaceId}/search?q=${encodeURIComponent(search)}`),
    enabled: search.trim().length > 1,
  });

  useEffect(() => {
    if (!containerRef.current || !data) return;
    const elements: cytoscape.ElementDefinition[] = [
      ...data.nodes.map((n: any) => ({
        data: {
          id: n.id, label: n.name.length > 26 ? `${n.name.slice(0, 24)}…` : n.name,
          type: n.type, color: NODE_COLORS[n.type] || "#94a3b8", size: NODE_SIZES[n.type] || 22,
          status: n.status,
        },
      })),
      ...data.edges.filter((e: any) => e.dst_id).map((e: any) => ({
        data: {
          id: e.id, source: e.src_id, target: e.dst_id, type: e.type,
          dashed: e.detection_method === "AI_INFERRED" || e.confidence < 0.75,
          color: e.detection_method === "AI_INFERRED" ? "#e879f9" : e.type === "PUBLISHES" || e.type === "CONSUMES" ? "#22d3ee" : "#475569",
        },
      })),
    ];
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        { selector: "node", style: {
          "background-color": "data(color)", "background-opacity": 0.16, "border-width": 1.5,
          "border-color": "data(color)", label: "data(label)", color: "#cbd5e1",
          "font-size": 9, "font-family": "JetBrains Mono, monospace", "text-valign": "bottom",
          "text-margin-y": 5, width: "data(size)", height: "data(size)", "text-max-width": "110px",
        } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#fff", "background-opacity": 0.35 } },
        { selector: "node[type='SERVICE']", style: { shape: "round-rectangle", "background-opacity": 0.22, "font-size": 10, "font-weight": "bold", color: "#e2e8f0" } },
        { selector: "node[type='KAFKA_TOPIC']", style: { shape: "diamond" } },
        { selector: "node[type='DATABASE']", style: { shape: "barrel" } },
        { selector: "node[type='API_ENDPOINT']", style: { shape: "hexagon" } },
        { selector: "edge", style: {
          width: 1.2, "line-color": "data(color)", "target-arrow-color": "data(color)",
          "target-arrow-shape": "triangle", "curve-style": "bezier", "arrow-scale": 0.7, opacity: 0.75,
          "line-style": "data(dashed)" as any, "line-dash-pattern": [6, 4],
        } },
        { selector: "edge[type='PUBLISHES'], edge[type='CONSUMES']", style: { width: 1.8, opacity: 0.95 } },
      ],
      layout: { name: layout as any, animate: false, padding: 40, nodeRepulsion: 9000, idealEdgeLength: 130 },
      wheelSensitivity: 0.2,
      minZoom: 0.15,
      maxZoom: 3,
    });
    cy.on("tap", "node", (event) => {
      const id = event.target.id();
      setSelected(id);
      setParams((prev) => { prev.set("node", id); return prev; }, { replace: true });
    });
    cy.on("dblclick", "node", (event) => {
      const id = event.target.id();
      setSeeds((prev) => (prev.includes(id) ? prev : [...prev, id]));
    });
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [data, layout]);

  const selectedNode = useMemo(() => data?.nodes?.find((n: any) => n.id === selected), [data, selected]);

  const findPath = async () => {
    if (!pathFrom || !pathTo) return;
    setBusy(true);
    try {
      setPathResult(await get<any>(`/workspaces/${workspaceId}/path?src=${pathFrom}&dst=${pathTo}`));
    } finally { setBusy(false); }
  };

  const analyzeImpact = async () => {
    if (!selected) return;
    setBusy(true);
    try { setImpact(await get<any>(`/workspaces/${workspaceId}/impact/${selected}?depth=2`)); }
    finally { setBusy(false); }
  };

  const toggle = (list: string[], setter: (v: string[]) => void, value: string) => {
    setter(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Architecture Explorer</h1>
          <p className="mt-1 text-xs text-slate-400">
            Progressive graph: the workspace view loads services and shared infrastructure first. Double-click a node to expand its neighbourhood.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => { setSeeds([]); setSelected(null); setParams({}, { replace: true }); }}>
            <Layers size={14} /> System view
          </Button>
          <Button size="sm" variant="outline" onClick={() => cyRef.current?.fit(undefined, 40)}><Maximize2 size={14} /> Fit</Button>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
        <Card className="relative overflow-hidden">
          <div className="flex flex-wrap items-center gap-2 border-b border-white/5 px-3 py-2">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Find node…" className="h-8 w-56 pl-8 text-xs" />
              {searchResults.data?.items?.length > 0 && search.trim().length > 1 && (
                <div className="absolute left-0 top-full z-30 mt-1 max-h-72 w-72 overflow-auto rounded-lg border border-white/10 bg-ink-850 p-1 shadow-2xl">
                  {searchResults.data.items.slice(0, 12).map((n: any) => (
                    <button key={n.id} onClick={() => { setSelected(n.id); setSeeds([n.id]); setSearch(""); }}
                      className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[11px] hover:bg-white/5">
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: NODE_COLORS[n.type] || "#94a3b8" }} />
                      <span className="truncate text-slate-200">{n.name}</span>
                      <span className="ml-auto text-slate-500">{n.type}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <Select value={String(depth)} onChange={(e) => setDepth(Number(e.target.value))} className="h-8 w-24 text-xs">
              <option value="1">depth 1</option><option value="2">depth 2</option><option value="3">depth 3</option>
            </Select>
            <Select value={layout} onChange={(e) => setLayout(e.target.value)} className="h-8 w-28 text-xs">
              <option value="cose">force</option><option value="concentric">concentric</option><option value="circle">circle</option><option value="grid">grid</option>
            </Select>
            <span className="ml-auto text-[10px] text-slate-500">
              {data?.nodes?.length || 0} nodes · {data?.edges?.length || 0} edges {data?.truncated && "· truncated"}
            </span>
            {isFetching && <Loader2 size={13} className="animate-spin text-accent-soft" />}
          </div>
          <div ref={containerRef} className="h-[560px] w-full bg-[radial-gradient(circle_at_center,rgba(99,102,241,.06),transparent_65%)]" />
          {isLoading && <div className="absolute inset-0 grid place-items-center bg-ink-950/40"><Spinner /></div>}
          <div className="flex flex-wrap items-center gap-1 border-t border-white/5 px-3 py-2 text-[10px]">
            {NODE_FILTERS.map((t) => (
              <button key={t} onClick={() => toggle(nodeTypes, setNodeTypes, t)}
                className={cn("rounded border px-1.5 py-0.5 transition",
                  nodeTypes.includes(t) ? "border-accent/60 bg-accent/15 text-white" : "border-white/10 text-slate-500 hover:text-slate-300")}>
                {t.replaceAll("_", " ")}
              </button>
            ))}
            <span className="mx-2 h-3 w-px bg-white/10" />
            {EDGE_FILTERS.slice(0, 6).map((t) => (
              <button key={t} onClick={() => toggle(edgeTypes, setEdgeTypes, t)}
                className={cn("rounded border px-1.5 py-0.5 transition",
                  edgeTypes.includes(t) ? "border-cyan-400/50 bg-cyan-400/10 text-cyan-200" : "border-white/10 text-slate-500 hover:text-slate-300")}>
                {t.replaceAll("_", " ")}
              </button>
            ))}
          </div>
        </Card>

        <div className="space-y-3">
          <Card>
            <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
              <h3 className="text-xs font-semibold text-slate-200">Inspector</h3>
              {selected && <button onClick={() => { setSelected(null); setImpact(null); }}><X size={13} className="text-slate-500" /></button>}
            </div>
            {!selected && <p className="p-4 text-[11px] text-slate-500">Select a node to inspect its evidence and provenance.</p>}
            {detail.isLoading && <Spinner label="" />}
            {detail.data && (
              <div className="space-y-3 p-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Badge tone="GENERATED">{detail.data.node.type}</Badge>
                    {detail.data.node.properties?.http_method && <Badge>{detail.data.node.properties.http_method}</Badge>}
                  </div>
                  <div className="mt-1.5 text-sm font-medium text-white break-words">{detail.data.node.name}</div>
                  <div className="mono mt-0.5 text-[10px] text-slate-500 break-all">{detail.data.node.qualified_name}</div>
                </div>
                <KeyValue items={[
                  ["Detection", detail.data.node.detection_method],
                  ["Confidence", `${Math.round((detail.data.node.confidence || 0) * 100)}%`],
                  ["File", detail.data.node.file_path?.split("/").pop()],
                  ["Line", detail.data.node.start_line],
                  ["Revision", fmt.commit(detail.data.node.source_revision)],
                  ["Status", detail.data.node.status],
                ]} />
                <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2 text-[10px] text-slate-400">
                  <div className="mb-1 text-slate-300">Evidence</div>
                  <div className="mono break-all text-slate-500">{detail.data.node.evidence?.file || "n/a"}</div>
                  <div className="mt-0.5">commit {fmt.commit(detail.data.node.evidence?.commit)} · {detail.data.node.evidence?.detection_method}</div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm" variant="outline" onClick={() => setShowSource(true)}><Eye size={12} /> Source</Button>
                  <Button size="sm" variant="outline" onClick={() => setSeeds((p) => [...new Set([...p, selected!])])}><Crosshair size={12} /> Expand</Button>
                  <Button size="sm" variant="outline" onClick={analyzeImpact} loading={busy}><Route size={12} /> Impact</Button>
                  <Button size="sm" variant="outline" onClick={() => setPathFrom(selected)}>From</Button>
                  <Button size="sm" variant="outline" onClick={() => setPathTo(selected)}>To</Button>
                </div>
                {detail.data.outgoing?.length > 0 && (
                  <div>
                    <div className="label mb-1">Outgoing ({detail.data.outgoing.length})</div>
                    <div className="max-h-32 space-y-0.5 overflow-auto">
                      {detail.data.outgoing.slice(0, 12).map((e: any) => (
                        <div key={e.id} className="flex gap-1.5 text-[10px]">
                          <span className="text-accent-soft">{e.type}</span>
                          <span className="truncate text-slate-400">{e.dst_name || e.dst_ref}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {pathResult && (
                  <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-2 text-[10px]">
                    <div className="mb-1 text-cyan-200">Dependency path</div>
                    {pathResult.found ? (
                      <div className="text-slate-300">{pathResult.path.map((n: any) => n.name).join(" → ")}</div>
                    ) : <div className="text-slate-400">No path found.</div>}
                  </div>
                )}
                {impact && (
                  <div className="rounded-lg border border-amber-400/20 bg-amber-400/5 p-2 text-[10px]">
                    <div className="mb-1 text-amber-200">{impact.impacted_count} impacted node(s)</div>
                    <div className="max-h-24 overflow-auto text-slate-400">
                      {impact.impacted.slice(0, 10).map((n: any) => <div key={n.id}>{n.type} · {n.name}</div>)}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>

          <Card className="p-3">
            <div className="mb-2 flex items-center gap-2 text-[11px] text-slate-400"><GitCompareArrows size={13} /> Path finder</div>
            <div className="space-y-1.5 text-[10px] text-slate-500">
              <div>from: <span className="text-slate-300">{pathFrom ? pathFrom.slice(0, 8) : "—"}</span></div>
              <div>to: <span className="text-slate-300">{pathTo ? pathTo.slice(0, 8) : "—"}</span></div>
            </div>
            <Button size="sm" variant="secondary" className="mt-2 w-full" disabled={!pathFrom || !pathTo} onClick={findPath} loading={busy}>
              Find shortest dependency path
            </Button>
          </Card>

          <Card className="p-3">
            <div className="mb-1 flex items-center gap-2 text-[11px] text-slate-400"><Filter size={12} /> Legend</div>
            <div className="flex flex-wrap gap-2 text-[10px] text-slate-500">
              {Object.entries(NODE_COLORS).slice(0, 8).map(([type, color]) => (
                <span key={type} className="inline-flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />{type.replaceAll("_", " ")}</span>
              ))}
            </div>
          </Card>
        </div>
      </div>
      {showSource && selected && <SourceViewer nodeId={selected} onClose={() => setShowSource(false)} />}
    </div>
  );
}
