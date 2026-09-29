import { useState } from "react";
import { validateLatest, validateUpload } from "../api/validateApi";
import ValidationActionCard from "../components/validation/ValidationActionCard";
import ValidationStatusPanel from "../components/validation/ValidationStatusPanel";
import ValidationSummaryCard from "../components/validation/ValidationSummaryCard";
import RuleResultCard from "../components/validation/RuleResultCard";

function ValidationPage() {
  const [status, setStatus] = useState("idle"); // idle | loading | success | error
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");

  const runValidation = async (fn) => {
    setStatus("loading");
    setError("");
    try {
      const data = await fn();
      setReport(data);
      setStatus("success");
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
          Validate combined files against the MARC / MBEW field mappings.
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
 