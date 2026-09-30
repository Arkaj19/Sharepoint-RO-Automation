import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  downloadSnapshot, getRefreshRun, listRefreshRuns, listSnapshots, previewSnapshot, startRefresh,
} from "../api/agentApi";
import PreviewTable from "../components/common/PreviewTable";
import { Badge, Button, Card, Empty, ErrorBox, PageHeader, StatusBadge, fmtDate } from "../components/common/ui";

const STAGES = ["list", "download", "combine", "profile", "diff", "agent", "done"];
const ACTIVE = ["queued", "running"];

function StageBar({ run }) {
  const idx = STAGES.indexOf(run.stage);
  const finished = !ACTIVE.includes(run.status);
  // stages the run really went through (every stage logs a message); a finished run
  // shows the others as skipped - e.g. diff and agent when nothing changed
  const visited = new Set((run.messages || []).map((m) => m.stage));
  return (
    <div className="flex flex-wrap gap-1.5">
      {STAGES.map((s, i) => {
        const reached = run.status !== "failed" && (i < idx || run.stage === "done");
        const done = reached && (!finished || visited.has(s) || s === "done");
        const skipped = finished && run.status !== "failed" && !done;
        const current = i === idx && ACTIVE.includes(run.status);
        return (
          <span
            key={s}
            title={skipped ? "skipped - nothing to do" : undefined}
            className={`text-[11px] px-2.5 py-1 rounded-full border ${
              current ? "bg-slate-900 text-white border-slate-900 animate-pulse"
                : done ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                  : skipped ? "bg-white text-gray-300 border-dashed border-gray-200 line-through"
                    : "bg-white text-gray-400 border-gray-200"
            }`}
          >
            {s}
          </span>
        );
      })}
    </div>
  );
}

function RunPanel({ run }) {
  return (
    <Card
      title={`Refresh ${run.refresh_run_id}`}
      subtitle={`Started ${fmtDate(run.started_at || run.created_at)}${run.force ? " · forced" : ""}`}
      actions={<StatusBadge status={run.status} />}
    >
      <StageBar run={run} />
      {run.error && <p className="text-xs text-red-700 mt-3">{run.error}</p>}
      {run.warnings?.length > 0 && (
        <ul className="mt-3 bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded-lg px-3 py-2 space-y-0.5">
          {run.warnings.map((w, i) => <li key={i}>⚠ {w}</li>)}
        </ul>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
        <div>
          <h4 className="text-xs font-medium text-gray-500 mb-2">Datasets</h4>
          <ul className="space-y-1 text-xs">
            {Object.entries(run.datasets || {}).map(([name, d]) => (
              <li key={name} className="flex items-start gap-2">
                <StatusBadge status={d.status} />
                <span className="font-medium text-gray-800">{name}</span>
                <span className="text-gray-500">
                  {d.status === "changed" && `${d.rows?.toLocaleString()} rows → ${d.version_id}`}
                  {d.status !== "changed" && d.status !== "error" && d.reason}
                  {d.status === "error" && d.error}
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="text-xs font-medium text-gray-500 mb-2">Agent 1</h4>
          {Object.keys(run.agent_runs || {}).length === 0 && (
            <p className="text-xs text-gray-500">{run.status === "no_changes" ? "Not run - nothing changed." : "—"}</p>
          )}
          <ul className="space-y-1 text-xs">
            {Object.entries(run.agent_runs || {}).map(([obj, a]) => (
              <li key={obj} className="flex flex-wrap items-center gap-2">
                <StatusBadge status={a.status} />
                <span className="font-medium">{obj}</span>
                {a.proposal_id && (
                  <Link to={`/proposals?object=${obj}`} className="text-slate-900 underline">
                    {a.operations} operation(s) to review
                  </Link>
                )}
                {a.error && <span className="text-red-700">{a.error}</span>}
              </li>
            ))}
          </ul>
        </div>
      </div>
      {run.messages?.length > 0 && (
        <ul className="mt-4 border-t border-gray-100 pt-3 space-y-0.5 text-[11px] text-gray-500 max-h-40 overflow-auto">
          {run.messages.map((m, i) => (
            <li key={i}>
              <span className="text-gray-400">{fmtDate(m.at)}</span> · {m.stage} · {m.message}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DataPage() {
  const [snapshots, setSnapshots] = useState([]);
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [force, setForce] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([listSnapshots(), listRefreshRuns(8)]);
      setSnapshots(s);
      setRuns(r);
      if (!activeRun && r.length) setActiveRun(r[0]);
    } catch (err) {
      setError(err.message);
    }
  }, [activeRun]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!activeRun || !ACTIVE.includes(activeRun.status)) return undefined;
    timer.current = setTimeout(async () => {
      try {
        const r = await getRefreshRun(activeRun.refresh_run_id);
        setActiveRun(r);
        if (!ACTIVE.includes(r.status)) load();
      } catch (err) {
        setError(err.message);
      }
    }, 2000);
    return () => clearTimeout(timer.current);
  }, [activeRun, load]);

  const handleRefresh = async () => {
    setError("");
    try {
      setActiveRun(await startRefresh(force));
    } catch (err) {
      setError(err.message);
    }
  };

  const showPreview = async (obj, side, version) => {
    setPreview({ obj, side, version, loading: true });
    try {
      setPreview({ obj, side, version, data: await previewSnapshot(obj, side, version, 20) });
    } catch (err) {
      setPreview({ obj, side, version, error: err.message });
    }
  };

  const running = activeRun && ACTIVE.includes(activeRun.status);

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader
        title="Data"
        subtitle="Pull the latest extracts from SharePoint. Each change is stored as a new version (current + 2 previous); Agent 1 then reviews what changed."
      >
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          Force (re-download and re-run even if nothing changed)
        </label>
        <Button onClick={handleRefresh} disabled={running}>
          {running ? "Refreshing…" : "Refresh data"}
        </Button>
      </PageHeader>

      <ErrorBox error={error} />
      {activeRun && <div className="mb-6"><RunPanel run={activeRun} /></div>}

      {snapshots.some((d) => d.supporting) && (
        <Card title="Supporting tables" className="mb-6"
              subtitle="Lookup tables the rules use. Looked for on SharePoint, then in backend/data/incoming/. Missing ones don't stop anything - rules that need them report 'input missing'.">
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-2 text-xs">
            {snapshots.filter((d) => d.supporting).map((d) => (
              <li key={d.side} className="flex items-start gap-2">
                {!d.versions.length ? <Badge tone="amber">missing</Badge>
                  : activeRun?.datasets?.[`${d.object}/${d.side}`]?.status === "kept"
                    ? <Badge tone="amber" title="The file is no longer in the source - the rules use this stored copy">stored copy only</Badge>
                    : <Badge tone="green">available</Badge>}
                <div>
                  <span className="font-medium text-gray-900">{d.side}</span>
                  {d.versions[0] && <span className="text-gray-500"> · {d.versions[0].rows?.toLocaleString()} rows · {d.versions[0].source}</span>}
                  <div className="text-gray-500">{d.description}</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Stored versions" subtitle="Live versions can be previewed and downloaded; older ones are zipped in the archive.">
        {snapshots.every((d) => d.versions.length === 0) ? (
          <Empty>No data stored yet. Click "Refresh data" to pull the extracts from SharePoint.</Empty>
        ) : (
          <div className="overflow-auto">
            <table className="min-w-full text-xs">
              <thead className="bg-gray-50">
                <tr className="text-left text-gray-600">
                  {["Dataset", "Version", "Stored", "Source", "Files", "Rows", "Cols", ""].map((h) => (
                    <th key={h} className="px-3 py-2 font-medium border-b border-gray-200">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {snapshots.map((d) =>
                  d.versions.map((v, i) => (
                    <tr key={`${d.object}-${d.side}-${v.version_id}`} className="border-b border-gray-100">
                      <td className="px-3 py-2 font-medium text-gray-800">
                        {i === 0 && (d.supporting ? `${d.side} (supporting)` : `${d.object} / ${d.side}`)}
                        {i === 0 && d.archived.length > 0 && (
                          <span className="ml-2 text-gray-400 font-normal">{d.archived.length} archived</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <span className="font-mono">{v.version_id}</span>{" "}
                        {v.current ? <Badge tone="green">current</Badge> : <Badge>previous</Badge>}
                      </td>
                      <td className="px-3 py-2 text-gray-600">{fmtDate(v.created_at)}</td>
                      <td className="px-3 py-2 text-gray-600">{v.source}</td>
                      <td className="px-3 py-2">{v.files}</td>
                      <td className="px-3 py-2">{v.rows?.toLocaleString()}</td>
                      <td className="px-3 py-2">{v.cols}</td>
                      <td className="px-3 py-2 whitespace-nowrap space-x-2">
                        <Button size="sm" variant="secondary" onClick={() => showPreview(d.object, d.side, v.version_id)}>
                          Preview
                        </Button>
                        <Button size="sm" variant="secondary"
                                onClick={() => downloadSnapshot(d.object, d.side, v.version_id).catch((e) => setError(e.message))}>
                          Download
                        </Button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
        {preview && (
          <div className="mt-4 border-t border-gray-100 pt-4">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold text-gray-700">
                Preview · {preview.obj}/{preview.side} · {preview.version}
              </h4>
              <button className="text-xs text-gray-500 hover:underline" onClick={() => setPreview(null)}>Close</button>
            </div>
            {preview.loading && <p className="text-xs text-gray-500 mt-2">Loading…</p>}
            {preview.error && <p className="text-xs text-red-700 mt-2">{preview.error}</p>}
            {preview.data && <PreviewTable preview={preview.data} />}
          </div>
        )}
      </Card>

      {runs.length > 0 && (
        <Card title="Recent refreshes" className="mt-6">
          <ul className="divide-y divide-gray-100 text-xs">
            {runs.map((r) => (
              <li key={r.refresh_run_id} className="py-2 flex flex-wrap items-center gap-3">
                <StatusBadge status={r.status} />
                <button className="font-mono text-slate-900 hover:underline" onClick={() => setActiveRun(r)}>
                  {r.refresh_run_id}
                </button>
                <span className="text-gray-500">{fmtDate(r.created_at)}</span>
                <span className="text-gray-500">
                  {Object.entries(r.datasets || {}).filter(([, d]) => d.status === "changed").map(([n]) => n).join(", ") || "no dataset changed"}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

export default DataPage;
