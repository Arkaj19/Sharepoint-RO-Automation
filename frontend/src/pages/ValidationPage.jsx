import { useState } from "react";
import { validateLatest, validateUpload } from "../api/validateApi";
import ValidationActionCard from "../components/validation/ValidationActionCard";
import ValidationStatusPanel from "../components/validation/ValidationStatusPanel";
import ValidationSummaryCard from "../components/validation/ValidationSummaryCard";
import RuleResultCard from "../components/validation/RuleResultCard";

const LAST_KEY = "validation:last";

function loadLast() {
  try {
    const raw = window.localStorage.getItem(LAST_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function ValidationPage() {
  // the last result is kept in this browser, so leaving the page or reloading doesn't lose it
  const last = loadLast();
  const [status, setStatus] = useState(last ? "success" : "idle"); // idle | loading | success | error
  const [report, setReport] = useState(last?.report || null);
  const [ranAt, setRanAt] = useState(last?.at || null);
  const [error, setError] = useState("");

  const runValidation = async (fn) => {
    setStatus("loading");
    setError("");
    try {
      const data = await fn();
      const at = new Date().toISOString();
      setReport(data);
      setRanAt(at);
      setStatus("success");
      try {
        window.localStorage.setItem(LAST_KEY, JSON.stringify({ at, report: data }));
      } catch {
        /* storage unavailable - the result still shows until the page is left */
      }
    } catch (err) {
      setError(err.message || "Something went wrong while validating.");
      setStatus("error");
    }
  };

  const handleRunLatest = (sheet) => runValidation(() => validateLatest(sheet));
  const handleRunUpload = (sheet, eccFile, s4File) =>
    runValidation(() => validateUpload(sheet, eccFile, s4File));

  return (
    <div className="max-w-[1600px] mx-auto px-6 py-8">
      <div className="mb-6">
        <h2 className="text-2xl font-semibold text-gray-900">Validation</h2>
        <p className="text-sm text-gray-500 mt-1">
          Validate the current ECC and S/4 versions against the YAML mapping (or two uploaded files).
        </p>
      </div>

      <div className="mb-6">
        <ValidationActionCard
          onRunLatest={handleRunLatest}
          onRunUpload={handleRunUpload}
          isLoading={status === "loading"}
        />
      </div>

      {status !== "success" && <ValidationStatusPanel status={status} error={error} />}

      {status === "success" && report && (
        <div className="space-y-4">
          {ranAt && (
            <p className="text-xs text-gray-500">
              Last run {new Date(ranAt).toLocaleString()} · {report.sheet} · {report.ecc_source} vs {report.s4_source}
              {" "}- run again after a refresh or an apply to see the current state.
            </p>
          )}
          <ValidationSummaryCard report={report} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {report.rules.map((rule) => (
              <RuleResultCard key={rule.rule_id} rule={rule} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default ValidationPage;
 