import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  getChangelog, getMapping, getMappingDiff, getMappingYaml, importLogic, listImports, listMappingVersions,
} from "../api/agentApi";
import {
  Badge, Button, Card, ConfidenceBadge, ErrorBox, OBJECTS, PageHeader, Segmented, StatusBadge, Tabs, fmtDate,
} from "../components/common/ui";

function ImportCard({ obj }) {
  const [last, setLast] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    listImports(obj).then((items) => setLast(items[0] || null)).catch(() => setLast(null));
  }, [obj]);
  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await importLogic(obj);
      setLast({ ...r, rows: r.main?.rows, variant_rows: r.variant?.rows, summary: r.main?.summary,
                created_at: new Date().toISOString() });
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
  return (
    <Card className="mb-4" title="Reference logic (Databricks SQL + business workbook)"
          subtitle="Import the Databricks views and the rule workbook as a proposal. Every rule is scored against the current S/4 output; conflicts with the workbook are flagged for review."
          actions={<Button size="sm" onClick={run} disabled={busy}>{busy ? "Importing… (≈30 s)" : "Import as proposal"}</Button>}>
      {error && <p className="text-xs text-red-700">{error}</p>}
      {last ? (
        <div className="text-xs text-gray-700 space-y-1">
          <div>Last import {fmtDate(last.created_at)} ·{" "}
            <Link to={`/proposals?object=${obj}`} className="underline">{last.proposal_id}</Link></div>
          {last.rows && <div>S/4 rows reproduced with the SQL as shared: <b>{pct(last.rows.theirs_reproduced_share)}</b>
            {last.variant_rows && <> · with commented-out lines restored: <b>{pct(last.variant_rows.theirs_reproduced_share)}</b></>}</div>}
          {last.summary && <div>{last.summary.fields_matching} fields match exactly, {last.summary.fields_mismatching} differ,
            {" "}{last.summary.fields_input_missing} need a missing input.</div>}
          {last.drift && <div className="text-amber-700">{last.drift}</div>}
        </div>
      ) : <p className="text-xs text-gray-500">Not imported yet.</p>}
    </Card>
  );
}

function FieldsTable({ fields, keyFields }) {
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const rows = fields.filter((f) =>
    (filter === "all" || (filter === "unmapped" && !f.ecc_column && !f.source_ref && f.logic_type === "N/A")
      || f.status === filter) &&
    (!q || `${f.id} ${f.s4_label} ${f.s4_column} ${f.ecc_column} ${f.source_ref}`.toLowerCase().includes(q.toLowerCase()))
  );
  return (
    <>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <Segmented value={filter} onChange={setFilter} options={[
          { value: "all", label: "All" }, { value: "unmapped", label: "No source" },
          { value: "approved", label: "Approved" }, { value: "migrated", label: "Migrated" }]} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search fields…"
               className="border border-gray-300 rounded px-3 py-1.5 text-xs w-64" />
        <span className="text-xs text-gray-500">{rows.length} of {fields.length}</span>
      </div>
      <div className="overflow-auto border border-gray-200 rounded-md max-h-[65vh]">
        <table className="min-w-full text-xs">
          <thead className="bg-gray-50 sticky top-0">
            <tr className="text-left text-gray-600">
              {["Field", "S/4 column", "Source", "Logic", "Type", "Status", "Reason"].map((h) => (
                <th key={h} className="px-3 py-2 font-medium border-b border-gray-200 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <tr key={f.id} className="border-b border-gray-100 align-top">
                <td className="px-3 py-2">
                  <div className="font-medium text-gray-900">{f.s4_label} {keyFields.includes(f.id) && <Badge tone="violet">key</Badge>}</div>
                  <div className="text-gray-400 font-mono">{f.id}</div>
                </td>
                <td className="px-3 py-2 font-mono">{f.s4_column}</td>
                <td className="px-3 py-2">
                  {f.source_ref && <div className="font-mono">{f.source_ref}</div>}
                  {f.ecc_column ? <div className="text-gray-500">{f.ecc_column}</div>
                    : !f.source_ref && <span className="text-amber-700">—</span>}
                </td>
                <td className="px-3 py-2 max-w-md">
                  <Badge tone={f.logic_type === "Derived" ? "blue" : f.logic_type === "Hardcoded" ? "violet" : "gray"}>{f.logic_type}</Badge>
                  <div className="whitespace-pre-wrap mt-1">{f.logic}</div>
                  {f.conflict && <div className="text-red-700 mt-1">Workbook conflict: {f.conflict}</div>}
                </td>
                <td className="px-3 py-2 whitespace-nowrap">{f.type}{f.length ? ` (${f.length})` : ""}{f.mandatory ? " *" : ""}</td>
                <td className="px-3 py-2 whitespace-nowrap"><StatusBadge status={f.status} /> <ConfidenceBadge value={f.confidence} />
                  {f.source && f.source !== "migration" && <div className="text-gray-400">{f.source}</div>}</td>
                <td className="px-3 py-2 text-gray-600 max-w-md">{f.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function MappingPage() {
  const [params, setParams] = useSearchParams();
  const obj = params.get("object") || "MARC";
  const [tab, setTab] = useState("fields");
  const [mapping, setMapping] = useState(null);
  const [yaml, setYaml] = useState("");
  const [versions, setVersions] = useState([]);
  const [diffFrom, setDiffFrom] = useState(null);
  const [diff, setDiff] = useState("");
  const [log, setLog] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    setMapping(null);
    Promise.all([getMapping(obj), listMappingVersions(obj), getChangelog(obj)])
      .then(([m, v, l]) => { setMapping(m); setVersions(v); setLog(l); })
      .catch((e) => setError(e.message));
  }, [obj]);

  useEffect(() => {
    if (tab === "yaml") getMappingYaml(obj).then(setYaml).catch((e) => setError(e.message));
  }, [tab, obj]);

  useEffect(() => {
    if (diffFrom) getMappingDiff(obj, diffFrom).then(setDiff).catch((e) => setError(e.message));
  }, [diffFrom, obj]);

  const valueMaps = useMemo(() => Object.entries(mapping?.value_maps || {}), [mapping]);

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader title="Mapping" subtitle="The YAML mapping - the single source of truth for the rule book and validation.">
        <Segmented options={OBJECTS} value={obj} onChange={(v) => setParams({ object: v })} />
      </PageHeader>
      <ErrorBox error={error} />
      {mapping && (
        <>
          <ImportCard obj={obj} />
          <Card className="mb-4" title={`${mapping.object} · ${mapping.title || ""} · v${mapping.mapping_version}`}
                subtitle={`Updated ${fmtDate(mapping.updated_at)} by ${mapping.updated_by} · key ${mapping.key.join(" + ")}`}>
            {mapping.hand_edited && (
              <p className="text-xs text-amber-700">
                The YAML file was edited outside the app. The next Apply includes those edits in the new version.
              </p>
            )}
            <p className="text-xs text-gray-600">
              {mapping.fields.filter((f) => f.logic_type !== "N/A").length} of {mapping.fields.length} fields have a rule or source ·{" "}
              {mapping.lookups.length} lookup(s) · {valueMaps.length} value map(s) · {mapping.filters.length} filter(s)
            </p>
            {mapping.lookups.length > 0 && (
              <ul className="text-xs mt-2 space-y-0.5">
                {mapping.lookups.map((lk) => (
                  <li key={lk.id}>
                    <Badge tone={lk.available ? "green" : "amber"}>{lk.available ? "available" : "input missing"}</Badge>{" "}
                    <span className="font-mono">{lk.text}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Tabs value={tab} onChange={setTab} tabs={[
            { value: "fields", label: "Fields", count: mapping.fields.length },
            { value: "maps", label: "Value maps & filters", count: valueMaps.length + mapping.filters.length
              + (mapping.ignored_columns?.ecc?.length || 0) + (mapping.ignored_columns?.s4?.length || 0) },
            { value: "yaml", label: "YAML" },
            { value: "history", label: "History", count: versions.length },
            { value: "log", label: "Change log", count: log.length },
          ]} />
          {tab === "fields" && <FieldsTable fields={mapping.fields} keyFields={mapping.key} />}
          {tab === "maps" && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {valueMaps.map(([id, vm]) => (
                <Card key={id} title={id} subtitle={`${vm.description || ""} · on missing: ${vm.on_missing}`}>
                  <ul className="text-xs grid grid-cols-2 gap-1">
                    {vm.entries.map((e, i) => (
                      <li key={i} className="font-mono">{e.from} → {e.to} <StatusBadge status={e.status} /></li>
                    ))}
                  </ul>
                </Card>
              ))}
              <Card title="Filters">
                {mapping.filters.length === 0 && <p className="text-xs text-gray-500">No filters.</p>}
                <ul className="text-xs space-y-2">
                  {mapping.filters.map((f) => (
                    <li key={f.id}><b className="font-mono">{f.id}</b> {f.text}
                      {f.source_text && <div className="text-gray-500">{f.source_text}</div>}</li>
                  ))}
                </ul>
              </Card>
              <Card title="Ignored columns"
                    subtitle="Extract columns deliberately not used by the mapping (approved via ignore_column).">
                {["ecc", "s4"].every((side) => !(mapping.ignored_columns?.[side] || []).length) && (
                  <p className="text-xs text-gray-500">None.</p>
                )}
                <ul className="text-xs space-y-1">
                  {["ecc", "s4"].flatMap((side) => (mapping.ignored_columns?.[side] || []).map((c) => (
                    <li key={`${side}-${c.column}`}>
                      <Badge>{side.toUpperCase()}</Badge> <span className="font-mono">{c.column}</span>
                      {c.reason && <span className="text-gray-500"> — {c.reason}</span>}
                      {c.provenance?.approved_by && <span className="text-gray-400"> · approved by {c.provenance.approved_by}</span>}
                    </li>
                  )))}
                </ul>
              </Card>
              {mapping.raw_condition_notes && (
                <Card title="Notes from the legacy mapping workbook"
                      subtitle="Reference text carried over from the old Excel mapping - not applied by the tool. The filters above are what runs.">
                  <pre className="text-[11px] bg-gray-50 border border-gray-200 rounded p-2 whitespace-pre-wrap">
                    {mapping.raw_condition_notes}
                  </pre>
                </Card>
              )}
            </div>
          )}
          {tab === "yaml" && (
            <pre className="bg-white border border-gray-200 rounded-lg p-4 text-[11px] leading-snug overflow-auto max-h-[70vh]">{yaml}</pre>
          )}
          {tab === "history" && (
            <Card title="Versions" subtitle="Pick a version to see what changed since then.">
              <ul className="text-xs space-y-1 mb-4">
                {versions.map((v) => (
                  <li key={v.version}>
                    <button className={`hover:underline ${diffFrom === v.version ? "font-semibold" : ""}`}
                            disabled={v.current} onClick={() => setDiffFrom(v.version)}>
                      v{v.version}
                    </button>{" "}
                    <span className="text-gray-500">{fmtDate(v.updated_at)} · {v.updated_by}</span>
                    {v.current && <Badge tone="green">current</Badge>}
                  </li>
                ))}
              </ul>
              {diff && (
                <pre className="text-[11px] leading-snug bg-gray-50 border border-gray-200 rounded p-3 overflow-auto max-h-[60vh]">
                  {diff.split("\n").map((line, i) => (
                    <div key={i} className={line.startsWith("+") ? "text-emerald-700" : line.startsWith("-") ? "text-red-700" : ""}>{line}</div>
                  ))}
                </pre>
              )}
            </Card>
          )}
          {tab === "log" && (
            <Card title="Applied operations">
              <ul className="divide-y divide-gray-100 text-xs">
                {log.map((r, i) => (
                  <li key={i} className="py-2">
                    <span className="text-gray-400">{fmtDate(r.at)}</span> · v{r.mapping_version} · <b>{r.op}</b> {r.target}
                    {r.decided_by && <span className="text-gray-500"> · by {r.decided_by}</span>}
                    <div className="text-gray-600">{r.reason}</div>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

export default MappingPage;
