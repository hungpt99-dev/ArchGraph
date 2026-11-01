import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Sparkles, Workflow } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, CardHeader, Empty, Spinner } from "../components/ui";
import { get } from "../lib/api";

export function Flows() {
  const { workspaceId = "" } = useParams();
  const { data, isLoading } = useQuery({
    queryKey: ["flows", workspaceId],
    queryFn: () => get<any[]>(`/workspaces/${workspaceId}/flows`),
  });

  if (isLoading) return <Spinner label="Loading flows…" />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-white">Business flows</h1>
          <p className="mt-1 max-w-2xl text-xs text-slate-400">
            Flows derived from Kafka producers/consumers are labelled <span className="text-slate-200">deterministic</span>.
            AI-generated flows are stored separately and marked <span className="text-fuchsia-300">inferred</span> until approved.
          </p>
        </div>
        <Link to={`/w/${workspaceId}/ai`} className="text-xs text-accent-soft hover:underline inline-flex items-center gap-1">
          <Sparkles size={12} /> Request an AI flow explanation
        </Link>
      </div>

      {data?.length === 0 && (
        <Empty title="No flows discovered yet" hint="Flows are derived when a topic has at least one producer and one consumer in the indexed graph." />
      )}

      <div className="space-y-3">
        {data?.map((flow) => (
          <Card key={flow.id}>
            <CardHeader
              title={flow.name}
              subtitle={flow.description}
              icon={<Workflow size={15} />}
              actions={<Badge tone={flow.detection_method === "AI_INFERRED" ? "AI_INFERRED" : "GENERATED"}>
                {flow.detection_method === "AI_INFERRED" ? "inferred" : "deterministic"} · {Math.round(flow.confidence * 100)}%
              </Badge>}
            />
            <div className="flex flex-wrap items-center gap-2 p-5">
              {flow.steps.map((step: any, index: number) => (
                <span key={`${flow.id}-${index}`} className="flex items-center gap-2">
                  <span className={`rounded-lg border px-3 py-1.5 text-[11px] ${
                    step.type === "KAFKA_TOPIC" ? "border-cyan-400/30 bg-cyan-400/10 text-cyan-100"
                    : step.type === "SERVICE" ? "border-accent/30 bg-accent/10 text-slate-100"
                    : "border-white/10 bg-white/[0.03] text-slate-200"}`}>
                    <span className="mr-1.5 text-[9px] uppercase tracking-wider text-slate-500">{step.layer || step.type}</span>
                    {step.name}
                    <span className="ml-1.5 text-[10px] text-slate-500">{step.action}</span>
                  </span>
                  {index < flow.steps.length - 1 && <ArrowRight size={12} className="text-slate-600" />}
                </span>
              ))}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
