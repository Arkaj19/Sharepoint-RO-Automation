import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { downloadOutput, generateOutput, getOutput, listOutputs } from "../api/agentApi";
import {
  Badge, Button, Card, Empty, ErrorBox, JsonView, OBJECTS, PageHeader, Segmented, Stat, StatusBadge, fmtDate,
} from "../components/common/ui";

const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
// agreement of a field: never shows 100.0% while there are mismatches (9 of 23,866 is 99.96%)
const agreementPct = (v, mismatches) => {
  if (v === null || v === undefined) return "—";
  const p = v * 100;
  if (mismatches > 0 && p >= 99.95) return p >= 99.995 ? ">99.99%" : `${p.toFixed(2)}%`;
  return `${p.toFixed(1)}%`;
};

const FIELD_TONE = { match: "green", mismatch: "amber", input_missing: "violet", no_rule: "red", not_compared: "gray" };

function FieldRow({ f }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="border-b border-gray-100 align-top">
        <td className="px-3 py-2">
          <div className="font-medium text-gray-900">{f.label}</div>
          <div className="text-gray-400 font-mono">{f.s4_column}</div>
        </td>
        <td className="px-3 py-2"><Badge tone={FIELD_TONE[f.status] || "gray"}>{f.status.replace("_", " ")}</Badge></td>
        <td className="px-3 py-2">{agreementPct(f.agreement, f.mismatches)}</td>
        <td className="px-3 py-2">{f.mismatches ?? "—"}</td>
        <td className="px-3 py-2 text-gray-600 max-w-md">
          {f.reason}
          {f.samples?.length > 0 && (
            <button className="text-slate-900 underline ml-1" onClick={() => setOpen((o) => !o)}>
              {open ? "hide examples" : "examples"}
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr className="bg-gray-50">
          <td colSpan={5} className="px-3 py-2">
            <table className="text-[11px]">
              <thead><tr className="text-gray-500"><th className="pr-4 text-left">Key</th><th className="pr-4 text-left">Ours</th><th className="text-left">Databricks</th></tr></thead>
              <tbody>
                {f.samples.map((s, i) => (
                  <tr key={i}>
                    <td className="pr-4 font-mono">{Object.values(s.key).join(" / ")}</td>
                    <td className="pr-4">{s.ours || "(blank)"}</td>
                    <td>{s.theirs || "(blank)"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

function OutputPage() {
  const [params, setParams] = useSearchParams();
  const obj = params.get("object") || "MARC";
  const [runs, setRuns] = useState([]);
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("problems");

  const load = useCallback(async (runId) => {
    setError("");
    try {
      const items = await listOutputs(obj);
      setRuns(items);
      const id = runId || items[0]?.run_id;
      setReport(id ? await getOutput(obj, id) : null);
    } catch (e) {
      setError(e.message);
    }
  }, [obj]);

  useEffect(() => { load(); }, [load]);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const r = await generateOutput(obj);
      await load(r.run_id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const shadow = report?.shadow;
  const fields = (shadow?.fields || []).filter((f) => filter === "all" || f.status !== "match");

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader
        title="Output"
        subtitle="Generate the S/4 load data ourselves from the ECC data and the approved rules, and compare it field by field with the Databricks output (shadow run)."
      >
        <Segmented options={OBJECTS} value={obj} onChange={(v) => setParams({ object: v })} />
        <Button onClick={run} disabled={busy}>{busy ? "Generating…" : "Generate & compare"}</Button>
      </PageHeader>
      <ErrorBox error={error} />
      {!report && !error && <Empty>No output generated for {obj} yet.</Empty>}
      {report && (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
          <div className="xl:col-span-3 space-y-6">
            <Card
              title={`S_${obj} · generated from mapping v${report.mapping_version}`}
              subtitle={`${report.run_id} · ${fmtDate(report.created_at)} · ECC ${report.snapshots?.ECC} vs S/4 ${report.snapshots?.S4}`
                + Object.entries(report.snapshots || {}).filter(([k]) => !["ECC", "S4"].includes(k))
                  .map(([k, v]) => ` · ${k.replace(/^REF\//, "")} ${v || "not delivered"}`).join("")}
              actions={!report.error && (
                <Button size="sm" variant="secondary" onClick={() => downloadOutput(obj, report.run_id).catch((e) => setError(e.message))}>
                  Download CSV
                </Button>
              )}
            >
              {report.error && <p className="text-sm text-red-700">{report.error}</p>}
              {shadow && (
                <dl className="grid grid-cols-2 md:grid-cols-6 gap-4">
                  <Stat label="Rows generated" value={report.rows_generated?.toLocaleString()} />
                  <Stat label="Databricks rows reproduced" value={pct(shadow.rows.theirs_reproduced_share)} />
                  <Stat label="Our rows confirmed" value={pct(shadow.rows.ours_confirmed_share)} />
                  <Stat label="Cell agreement" value={pct(shadow.summary.cell_agreement)} />
                  <Stat label="Fields matching" value={`${shadow.summary.fields_matching} / ${shadow.summary.fields_total}`} />
                  <Stat label="Need an input" value={shadow.summary.fields_input_missing}
                        tone={shadow.summary.fields_input_missing ? "amber" : undefined} />
                </dl>
              )}
              {report.missing_inputs?.length > 0 && (
                <p className="text-xs text-violet-700 mt-3">
                  Tables not delivered yet: {report.missing_inputs.join(", ")} — the fields that need them are left blank.
                </p>
              )}
              {shadow && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 text-xs">
                  <div>
                    <b>Only in ours</b> ({shadow.rows.keys_only_ours.toLocaleString()}) — usually scope rules we can't apply yet
                    <JsonView value={shadow.rows.sample_only_ours} label="Examples" />
                  </div>
                  <div>
                    <b>Only in Databricks</b> ({shadow.rows.keys_only_theirs.toLocaleString()})
                    <JsonView value={shadow.rows.sample_only_theirs} label="Examples" />
                  </div>
                </div>
              )}
            </Card>

            {shadow && (
              <Card title="Fields" subtitle="Compared after normalising formatting ('0.000' = '0', any date format). Examples show our value next to Databricks'."
                    actions={<Segmented value={filter} onChange={setFilter}
                                        options={[{ value: "problems", label: "Differences" }, { value: "all", label: "All fields" }]} />}>
                <div className="overflow-auto">
                  <table className="min-w-full text-xs">
                    <thead className="bg-gray-50">
                      <tr className="text-left text-gray-600">
                        {["Field", "Status", "Agreement", "Mismatches", "Notes"].map((h) => (
                          <th key={h} className="px-3 py-2 font-medium border-b border-gray-200">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>{fields.map((f) => <FieldRow key={f.field} f={f} />)}</tbody>
                  </table>
                </div>
              </Card>
            )}
            {report.filters?.length > 0 && (
              <Card title="Filters applied">
                <ul className="text-xs space-y-1">
                  {report.filters.map((f) => (
                    <li key={f.filter_id}><span className="font-mono">{f.filter_id}</span> — {f.rows_removed !== undefined
                      ? `${f.rows_removed.toLocaleString()} row${f.rows_removed === 1 ? "" : "s"} removed` : f.input_missing ? `input missing: ${f.input_missing}` : f.error}
                      {f.samples?.length > 0 && (
                        <span className="text-gray-500"> (e.g. {f.samples.map((k) => Object.values(k).join(" / ")).join(", ")})</span>
                      )}</li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
          <Card title="Runs">
            <ul className="space-y-2 text-xs">
              {runs.map((r) => (
                <li key={r.run_id}>
                  <button onClick={() => load(r.run_id)}
                          className={`hover:underline text-left ${r.run_id === report.run_id ? "font-semibold" : ""}`}>
                    {fmtDate(r.created_at)}
                  </button>
                  <div className="text-gray-500">
                    v{r.mapping_version} · {pct(r.rows?.theirs_reproduced_share)} rows · {pct(r.summary?.cell_agreement)} cells
                    {r.error && <StatusBadge status="error" />}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}

export default OutputPage;
