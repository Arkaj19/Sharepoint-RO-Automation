import { useState } from "react";
import { validateLatest, validateUpload } from "../api/validateApi";
import ValidationActionCard from "../components/validation/ValidationActionCard";
import ValidationStatusPanel from "../components/validation/ValidationStatusPanel";
import ValidationSummaryCard from "../components/validation/ValidationSummaryCard";
import RuleResultCard from "../components/validation/RuleResultCard";
import TransformationPreview from "../components/validation/TransformationPreview";

// Materials used for the POC demo (FERT, split plants, MARM conversion)
const DEMO_MATNRS = [
  "7079800107", "7079800120", "7079800124", "7079800127",
  "7079800129", "7079800130", "7079800131",
];

function ValidationPage() {
  const [status, setStatus] = useState("idle");
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [previewSheet, setPreviewSheet] = useState(null); // "MARC" | "MBEW" | null

  const runValidation = async (fn, sheet) => {
    setStatus("loading");
    setError("");
    setPreviewSheet(null);
    try {
      const data = await fn();
      setReport(data);
      setStatus("success");
      setPreviewSheet(sheet);
    } catch (err) {
      setError(err.message || "Something went wrong while validating.");
      setStatus("error");
    }
  };

  const handleRunLatest = (sheet) =>
    runValidation(() => validateLatest(sheet), sheet);

  const handleRunUpload = (sheet, eccFile, s4File) =>
    runValidation(() => validateUpload(sheet, eccFile, s4File), sheet);

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
          {previewSheet === "MARC" && (
            <div className="mb-4">
              <TransformationPreview tableType="MARC" matnrs={DEMO_MATNRS} />
            </div>
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