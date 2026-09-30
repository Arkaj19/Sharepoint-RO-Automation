import { useEffect, useState } from "react";
import { downloadRuleBook, generateRuleBook, listMappings, listRuleBooks } from "../api/agentApi";
import { Button, Card, Empty, ErrorBox, PageHeader, fmtDate } from "../components/common/ui";

function RuleBookPage() {
  const [files, setFiles] = useState([]);
  const [mappings, setMappings] = useState([]);
  const [drafts, setDrafts] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = () => Promise.all([listRuleBooks(), listMappings()])
    .then(([f, m]) => { setFiles(f); setMappings(m); })
    .catch((e) => setError(e.message));

  useEffect(() => { load(); }, []);

  const generate = async () => {
    setBusy(true);
    setError("");
    try {
      const out = await generateRuleBook(drafts);
      await load();
      await downloadRuleBook(out.name);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <PageHeader title="Rule Book" subtitle="Excel rule book generated from the YAML mappings: one sheet per object, plus value maps, filters and the change log.">
        <label className="flex items-center gap-2 text-xs text-gray-600">
          <input type="checkbox" checked={drafts} onChange={(e) => setDrafts(e.target.checked)} />
          Include pending proposal operations as Draft rows
        </label>
        <Button onClick={generate} disabled={busy}>{busy ? "Generating…" : "Generate & download"}</Button>
      </PageHeader>
      <ErrorBox error={error} />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Card title="Mappings included" className="lg:col-span-1">
          <ul className="text-xs space-y-2">
            {mappings.map((m) => (
              <li key={m.object}>
                <b>{m.object}</b> v{m.mapping_version} · {m.approved} approved / {m.fields} fields ·{" "}
                {m.ecc_mapped} with ECC column
                <div className="text-gray-500">updated {fmtDate(m.updated_at)} by {m.updated_by}</div>
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Generated rule books" subtitle="The last 10 are kept." className="lg:col-span-2">
          {files.length === 0 ? <Empty>No rule book generated yet.</Empty> : (
            <ul className="divide-y divide-gray-100 text-xs">
              {files.map((f) => (
                <li key={f.name} className="py-2 flex items-center justify-between gap-3">
                  <div>
                    <div className="font-mono text-gray-900">{f.name}</div>
                    <div className="text-gray-500">{fmtDate(f.created_at)} · {(f.size / 1024).toFixed(0)} KB</div>
                  </div>
                  <Button size="sm" variant="secondary" onClick={() => downloadRuleBook(f.name).catch((e) => setError(e.message))}>
                    Download
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

export default RuleBookPage;
