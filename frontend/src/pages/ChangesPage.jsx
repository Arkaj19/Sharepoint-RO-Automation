import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { getChangeset, listChangesets } from "../api/agentApi";
import {
  Badge, Card, Empty, ErrorBox, JsonView, OBJECTS, PageHeader, Segmented, Stat, StatusBadge, fmtDate,
} from "../components/common/ui";

function describe(ch) {
  switch (ch.kind) {
    case "column_renamed":
      return `"${ch.from}" → "${ch.to}" (similarity ${Math.round(ch.score * 100)}%)`;
    case "column_added":
    case "column_removed":
    case "blank_rate_changed":
    case "type_changed":
    case "length_increased":
    case "distinct_changed":
      return `${ch.column}${ch.before !== undefined ? `: ${ch.before} → ${ch.after}` : ""}`;
    case "new_code_values":
    case "removed_code_values":
      return `${ch.column}: ${Object.entries(ch.values).map(([v, n]) => `${v} (${n})`).join(", ")}`;
    case "row_count_changed":
      return `${ch.before.toLocaleString()} → ${ch.after.toLocaleString()} (${ch.delta > 0 ? "+" : ""}${ch.delta})`;
    case "keys_added":
    case "keys_removed":
      return `${ch.count.toLocaleString()} key(s), e.g. ${ch.samples.slice(0, 3).join(", ")}`;
    case "cells_changed":
      return `${ch.columns_changed} column(s) changed across ${ch.keys_compared.toLocaleString()} common keys: ` +
        ch.columns.slice(0, 6).map((c) => `${c.column} (${c.changed})`).join(", ");
    case "file_added":
    case "file_removed":
    case "file_changed":
      return ch.file;
    case "workbook_changed":
      return `Business rule workbook updated - ${ch.hint}`;
    case "table_added":
      return `${ch.table}: delivered (${(ch.rows ?? 0).toLocaleString()} rows) - rules that use it no longer report "input missing"`;
    default:
      return "";
  }
}

function HealthList({ title, items, render }) {
  if (!items || items.length === 0) return null;
  return (
    <div>
      <h4 className="text-xs font-medium text-gray-500 mb-1">{title} ({items.length})</h4>
      <div className="flex flex-wrap gap-1">
        {items.slice(0, 60).map((x, i) => <Badge key={i}>{render ? render(x) : x}</Badge>)}
        {items.length > 60 && <span className="text-xs text-gray-400">+{items.length - 60} more</span>}
      </div>
    </div>
  );
}

function ChangesPage() {
  const [params, setParams] = useSearchParams();
  const obj = params.get("object") || "MARC";
  const [list, setList] = useState([]);
  const [cs, setCs] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    setCs(null);
    listChangesets(obj)
      .then(async (items) => {
        setList(items);
        if (items.length) setCs(await getChangeset(items[0].changeset_id));
      })
      .catch((e) => setError(e.message));
  }, [obj]);

  const select = (id) => getChangeset(id).then(setCs).catch((e) => setError(e.message));
  const h = cs?.mapping_health;

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader title="Changes" subtitle="What changed between the previous and current extract, and where the mapping no longer fits the data.">
        <Segmented options={OBJECTS} value={obj} onChange={(v) => setParams({ object: v })} />
      </PageHeader>
      <ErrorBox error={error} />
      {!cs && !error && <Empty>No change sets for {obj} yet. They are created by "Refresh data" or "Re-run agent".</Empty>}
      {cs && (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
          <div className="xl:col-span-3 space-y-6">
            <Card
              title={`${cs.object} · ${cs.baseline ? "baseline (first run)" : "changes since previous version"}`}
              subtitle={`${cs.changeset_id} · ${fmtDate(cs.created_at)} · mapping v${cs.mapping_version}`}
              actions={<Link to={`/proposals?object=${cs.object}`} className="text-xs font-medium text-slate-900 underline">Agent 1 proposal →</Link>}
            >
              <dl className="grid grid-cols-2 md:grid-cols-6 gap-4">
                <Stat label="Changes" value={cs.summary.changes} />
                <Stat label="Affect mapping" value={cs.summary.mapping_impacts} tone={cs.summary.mapping_impacts ? "amber" : undefined} />
                <Stat label="Unmapped fields" value={cs.summary.unmapped_fields} tone={cs.summary.unmapped_fields ? "amber" : undefined} />
                <Stat label="Broken columns" value={cs.summary.broken_columns} tone={cs.summary.broken_columns ? "red" : undefined} />
                <Stat label="Low agreement" value={cs.summary.low_agreement_fields} tone={cs.summary.low_agreement_fields ? "amber" : undefined} />
                <Stat label="Crosswalk gaps" value={cs.summary.crosswalk_gaps} tone={cs.summary.crosswalk_gaps ? "red" : undefined} />
              </dl>
              <div className="flex flex-wrap gap-4 mt-4 text-xs text-gray-600">
                {Object.entries(cs.datasets).map(([side, d]) => (
                  <span key={side}>
                    <b>{side}</b> <StatusBadge status={d.status} /> {d.from ? `${d.from} → ` : ""}{d.to}
                  </span>
                ))}
              </div>
            </Card>

            <Card title="Changes" subtitle="Ranked by severity; chips show the mapped fields and value maps each change touches.">
              {cs.changes.length === 0 ? (
                <p className="text-xs text-gray-500">
                  {cs.baseline ? "First version - nothing to compare yet. The mapping health below drives the bootstrap proposal." : "No differences."}
                </p>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {cs.changes.map((ch) => (
                    <li key={ch.id} className="py-2.5 text-xs">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge status={ch.severity} />
                        <span className="font-medium text-gray-900">{ch.kind.replaceAll("_", " ")}</span>
                        <Badge tone="violet">{ch.side}</Badge>
                        <span className="text-gray-700">{describe(ch)}</span>
                        {ch.impacts?.map((im) => <Badge key={im} tone="amber">{im}</Badge>)}
                      </div>
                      <JsonView value={ch} label="Detail" />
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card title="Mapping health" subtitle="Current mapping checked against the current data.">
              <div className="space-y-4">
                <HealthList title="Fields without an ECC column" items={h.unmapped_fields} />
                <HealthList title="Columns in the mapping that no longer exist" items={h.broken_columns}
                            render={(b) => `${b.side}: ${b.column} (${b.field})`} />
                <HealthList title="Fields whose values no longer agree" items={h.low_agreement_fields}
                            render={(f) => `${f.field} ${Math.round(f.agreement * 100)}%`} />
                <HealthList title="S/4 columns not in the mapping" items={h.unmapped_s4_columns} />
                {Object.entries(h.crosswalk_gaps || {}).map(([fid, g]) => (
                  <HealthList key={fid} title={`${fid}: ECC codes missing from ${g.value_map}`}
                              items={Object.entries(g.unmapped_values)} render={([v, n]) => `${v} (${n} rows)`} />
                ))}
                {h.row_counts && !h.row_counts.error && (
                  <p className="text-xs text-gray-600">
                    Rows: ECC {h.row_counts.ecc_rows.toLocaleString()} → expected in S/4 {h.row_counts.ecc_rows_expected.toLocaleString()} (after fan-out and filters)
                    vs S/4 {h.row_counts.s4_rows_after_filters.toLocaleString()} · gap {h.row_counts.gap}
                  </p>
                )}
              </div>
            </Card>
          </div>

          <div className="space-y-6">
            <Card title="Alignment" subtitle="How ECC and S/4 rows pair up for the evidence.">
              <dl className="grid grid-cols-2 gap-3">
                <Stat label="Matched pairs" value={cs.alignment.stats.matched_pairs?.toLocaleString()} />
                <Stat label="S/4 rows matched" value={cs.alignment.stats.s4_rows_matched_share !== undefined ? `${Math.round(cs.alignment.stats.s4_rows_matched_share * 100)}%` : "—"} />
              </dl>
              <ul className="mt-3 space-y-1 text-xs text-gray-600 list-disc pl-4">
                {cs.alignment.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </Card>
            <Card title="History">
              <ul className="space-y-1 text-xs">
                {list.map((c) => (
                  <li key={c.changeset_id}>
                    <button onClick={() => select(c.changeset_id)}
                            className={`hover:underline ${c.changeset_id === cs.changeset_id ? "font-semibold" : ""}`}>
                      {fmtDate(c.created_at)}
                    </button>{" "}
                    <span className="text-gray-500">{c.baseline ? "baseline" : `${c.summary.changes} changes`}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

export default ChangesPage;
