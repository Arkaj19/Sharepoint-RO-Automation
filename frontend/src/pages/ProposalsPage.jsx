import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { applyProposal, decideOps, getProposal, listProposals, rerunAgent } from "../api/agentApi";
import {
  Badge, Button, Card, ConfidenceBadge, Empty, ErrorBox, JsonView, OBJECTS, PageHeader, Segmented,
  StatusBadge, Tabs, fmtDate, useReviewer,
} from "../components/common/ui";

const OP_LABELS = {
  set_column_alias: "Map column",
  update_field_attrs: "Change field attributes",
  add_field: "Add field",
  remove_field: "Remove field",
  update_key: "Change key",
  ignore_column: "Ignore column",
  set_transform: "Set transform",
  add_split_rule: "Add split rule",
  set_default: "Set default",
  add_value_map_entries: "Add value map entries",
  remove_value_map_entries: "Remove value map entries",
  add_filter: "Add filter",
  remove_filter: "Remove filter",
  set_dictionary: "Set column dictionary",
  set_lookup: "Add lookup table",
  set_source_ref: "Set source",
};

function Side({ label, value }) {
  if (value === null || value === undefined) return (
    <div><div className="text-[11px] text-gray-400 mb-1">{label}</div><div className="text-xs text-gray-400">—</div></div>
  );
  let body;
  if (typeof value === "string") body = <div className="text-xs text-gray-800">{value}</div>;
  else if (Array.isArray(value)) body = (
    <ul className="text-xs text-gray-800 max-h-40 overflow-auto">{value.map((v, i) => <li key={i}>{String(v)}</li>)}</ul>
  );
  else body = (
    <dl className="text-xs grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5">
      {Object.entries(value).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-gray-500">{k}</dt>
          <dd className="text-gray-800 break-words">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
  return <div><div className="text-[11px] text-gray-400 mb-1">{label}</div>{body}</div>;
}

function OpCard({ op, onDecide, busy, locked }) {
  const decided = ["accepted", "rejected"].includes(op.status);
  return (
    <div className={`bg-white border rounded-lg p-4 ${op.status === "accepted" ? "border-emerald-300" : op.status === "rejected" ? "border-red-200 opacity-70" : "border-gray-200"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-mono text-gray-400">{op.op_id}</span>
        <span className="text-sm font-semibold text-gray-900">{OP_LABELS[op.op] || op.op}</span>
        <span className="text-xs text-gray-600">
          {op.preview?.target || (op.field_id && `field ${op.field_id}`) || op.map_id || op.filter?.id || op.filter_id
            || (op.table && `dictionary ${op.table}`) || op.lookup?.id || op.column}
        </span>
        <ConfidenceBadge value={op.confidence} />
        <StatusBadge status={op.status} />
        {op.source === "llm" && <Badge tone="violet">added by model</Badge>}
        {op.source === "databricks" && <Badge tone="blue">from Databricks SQL</Badge>}
        {op.alternative_group && <Badge tone="amber" title={op.alternative_group}>alternative — accept one</Badge>}
        {op.reference?.conflict && <Badge tone="red">workbook conflict</Badge>}
        {op.depends_on?.length > 0 && <span className="text-[11px] text-gray-400">needs {op.depends_on.join(", ")}</span>}
        <div className="ml-auto flex gap-2">
          {!locked && op.status !== "applied" && (
            <>
              <Button size="sm" variant="accept" disabled={busy || op.status === "accepted"} onClick={() => onDecide(op, "accepted")}>Accept</Button>
              <Button size="sm" variant="reject" disabled={busy || op.status === "rejected"} onClick={() => onDecide(op, "rejected")}>Reject</Button>
              {decided && <Button size="sm" variant="secondary" disabled={busy} onClick={() => onDecide(op, "pending")}>Undo</Button>}
            </>
          )}
        </div>
      </div>
      <p className="text-xs text-gray-700 mt-2">{op.reason}</p>
      {op.reference?.conflict && (
        <div className="mt-2 text-xs bg-red-50 border border-red-200 rounded p-2">
          <div className="font-medium text-red-800">{op.reference.conflict.detail}</div>
          {op.reference.workbook?.logic && (
            <div className="mt-1 text-red-900 whitespace-pre-wrap"><b>Workbook:</b> {op.reference.workbook.logic}</div>
          )}
        </div>
      )}
      {op.reference?.restored_lines && (
        <div className="mt-2 text-xs bg-amber-50 border border-amber-200 rounded p-2">
          <b>Commented-out SQL lines restored in this variant:</b>
          {Object.entries(op.reference.restored_lines).map(([block, lines]) => (
            <div key={block} className="mt-1"><span className="font-mono">{block}</span>: {lines.join(" · ")}</div>
          ))}
        </div>
      )}
      {op.result && (
        <p className={`text-[11px] mt-1 ${["failed", "stale"].includes(op.status) ? "text-amber-700" : "text-gray-500"}`}>
          {op.result}
        </p>
      )}
      {op.preview && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3 bg-gray-50 rounded p-3">
          <Side label="Before" value={op.preview.before} />
          <Side label="After" value={op.preview.after} />
        </div>
      )}
      <JsonView value={op.evidence} />
    </div>
  );
}

function ProposalsPage() {
  const [params, setParams] = useSearchParams();
  const obj = params.get("object") || "MARC";
  const [list, setList] = useState([]);
  const [proposal, setProposal] = useState(null);
  const [tab, setTab] = useState("G1");
  const [minConf, setMinConf] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [reviewer, setReviewer] = useReviewer();

  const load = useCallback(async (pid) => {
    setError("");
    try {
      const items = await listProposals(obj);
      setList(items);
      const target = pid || items.find((p) => p.status === "open")?.proposal_id || items[0]?.proposal_id;
      setProposal(target ? await getProposal(target) : null);
    } catch (e) {
      setError(e.message);
    }
  }, [obj]);

  useEffect(() => { load(); }, [load]);

  const locked = proposal && proposal.status === "superseded";
  const decide = async (decisions) => {
    setBusy(true);
    setError("");
    try {
      setProposal(await decideOps(proposal.proposal_id, decisions, reviewer));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const acceptConfident = () => decide(
    proposal.ops.filter((o) => o.status === "pending" && o.confidence >= 0.9 && o.gate === tab && !o.alternative_group
      && !o.reference?.conflict)
      .map((o) => ({ op_id: o.op_id, decision: "accepted" }))
  );

  const [applying, setApplying] = useState(false);
  const apply = async () => {
    setBusy(true);
    setApplying(true);
    setError("");
    const n = proposal.ops.filter((o) => o.status === "accepted").length;
    setInfo(`Applying ${n} operation(s) to the ${proposal.object} mapping - this can take up to half a minute, please stay on this page…`);
    try {
      const p = await applyProposal(proposal.proposal_id, reviewer);
      const last = p.applications[p.applications.length - 1];
      setProposal(p);
      const failed = p.ops.filter((o) => ["failed", "stale"].includes(o.status)).length;
      setInfo(last.mapping_version
        ? `Applied ${last.applied.length} operation(s) - ${p.object} mapping is now v${last.mapping_version}.`
          + (failed ? ` ${failed} operation(s) failed or are stale - see their messages below.` : "")
        : "Nothing was applied (see failed / stale operations).");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
      setApplying(false);
    }
  };

  const rerun = async () => {
    setError("");
    try {
      await rerunAgent(obj);
      setInfo(`Agent 1 is re-running for ${obj}. This takes a minute or two - reload to see the new proposal.`);
    } catch (e) {
      setError(e.message);
    }
  };

  const ops = proposal?.ops || [];
  const shown = ops.filter((o) => o.gate === tab && (o.confidence ?? 0) >= minConf);
  const accepted = ops.filter((o) => o.status === "accepted").length;
  const counts = ops.reduce((acc, o) => ({ ...acc, [o.status]: (acc[o.status] || 0) + 1 }), {});

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader title="Proposals" subtitle="Agent 1's proposed mapping changes. Nothing reaches the mapping until you accept it and click Apply.">
        <Segmented options={OBJECTS} value={obj} onChange={(v) => setParams({ object: v })} />
        <Button variant="secondary" onClick={rerun}>Re-run agent</Button>
      </PageHeader>
      <ErrorBox error={error} />
      {info && <div className="bg-sky-50 border border-sky-200 text-sky-800 text-sm rounded-lg px-4 py-3 mb-4">{info}</div>}
      {!proposal && !error && <Empty>No proposals for {obj} yet. Run "Refresh data" or "Re-run agent".</Empty>}

      {proposal && (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
          <div className="xl:col-span-3 space-y-4">
            <Card
              title={`${proposal.object} · ${proposal.mode} proposal`}
              subtitle={`${proposal.proposal_id} · ${fmtDate(proposal.created_at)} · based on mapping v${proposal.base_mapping_version}`}
              actions={<StatusBadge status={proposal.status} />}
            >
              <p className="text-sm text-gray-800 whitespace-pre-wrap">{proposal.summary}</p>
              <div className="flex flex-wrap items-center gap-2 mt-4 text-xs text-gray-600">
                {Object.entries(counts).map(([s, n]) => <span key={s}><StatusBadge status={s} /> {n}</span>)}
              </div>
              {!locked && (
                <div className="flex flex-wrap items-center gap-3 mt-4 border-t border-gray-100 pt-4">
                  <label className="text-xs text-gray-600 flex items-center gap-2">
                    Reviewer
                    <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="your name"
                           className="border border-gray-300 rounded px-2 py-1 text-xs" />
                  </label>
                  <Button variant="secondary" size="sm" disabled={busy} onClick={acceptConfident}
                          title="Skips alternatives and workbook conflicts - decide those one by one">
                    Accept all pending {tab} ≥ 90%
                  </Button>
                  <Button disabled={busy || accepted === 0} onClick={apply}>{applying ? "Applying…" : `Apply accepted (${accepted})`}</Button>
                </div>
              )}
              {locked && <p className="text-xs text-amber-700 mt-3">Superseded by {proposal.superseded_by} - review that one instead.</p>}
            </Card>

            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { value: "G1", label: "G1 · Mappings", count: ops.filter((o) => o.gate === "G1").length },
                { value: "G2", label: "G2 · Rules", count: ops.filter((o) => o.gate === "G2").length },
                { value: "unresolved", label: "Needs a decision", count: (proposal.unresolved || []).length },
                { value: "dropped", label: "Dropped", count: (proposal.dropped_ops || []).length },
              ]}
            />

            {(tab === "G1" || tab === "G2") && (
              <>
                <div className="flex items-center gap-3 text-xs text-gray-600">
                  Minimum confidence
                  <Segmented value={minConf} onChange={setMinConf}
                             options={[{ value: 0, label: "All" }, { value: 0.7, label: "≥ 70%" }, { value: 0.9, label: "≥ 90%" }]} />
                  <span>{shown.length} shown</span>
                </div>
                <div className="space-y-3">
                  {shown.map((op) => (
                    <OpCard key={op.op_id} op={op} busy={busy} locked={locked}
                            onDecide={(o, d) => decide([{ op_id: o.op_id, decision: d }])} />
                  ))}
                </div>
              </>
            )}
            {tab === "unresolved" && (
              <Card title="Items the agent left for a human" subtitle="No confident evidence either way - decide in the YAML or with the migration owners.">
                <ul className="divide-y divide-gray-100 text-xs">
                  {(proposal.unresolved || []).map((u, i) => (
                    <li key={i} className="py-2">
                      <Badge tone="amber">{u.kind}</Badge> <b>{u.field}</b> {u.s4_label && `(${u.s4_label})`}
                      <JsonView value={u} label="Detail" />
                    </li>
                  ))}
                </ul>
              </Card>
            )}
            {tab === "dropped" && (
              <Card title="Dropped operations" subtitle="Removed by the model or by validation, with the reason.">
                <ul className="divide-y divide-gray-100 text-xs">
                  {(proposal.dropped_ops || []).map((d, i) => (
                    <li key={i} className="py-2">
                      <Badge>{d.by}</Badge> <b>{OP_LABELS[d.op.op] || d.op.op}</b> {d.op.field_id || d.op.map_id || d.op.column} — {d.reason}
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>

          <Card title="All proposals">
            <ul className="space-y-2 text-xs">
              {list.map((p) => (
                <li key={p.proposal_id}>
                  <button onClick={() => load(p.proposal_id)}
                          className={`hover:underline text-left ${p.proposal_id === proposal.proposal_id ? "font-semibold" : ""}`}>
                    {fmtDate(p.created_at)}
                  </button>{" "}
                  <StatusBadge status={p.status} />
                  <div className="text-gray-500">{p.mode} · {p.ops_total} ops</div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}

export default ProposalsPage;
