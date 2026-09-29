import { useState } from "react";

const SHEETS = [
  { value: "MARC", label: "MARC (Plant Data)" },
  { value: "MBEW", label: "MBEW (Valuation Data)" },
];

function ValidationActionCard({ onRunLatest, onRunUpload, isLoading }) {
  const [sheet, setSheet] = useState("MARC");
  const [mode, setMode] = useState("latest"); // latest | upload
  const [eccFile, setEccFile] = useState(null);
  const [s4File, setS4File] = useState(null);

  const canRunUpload = eccFile && s4File && !isLoading;

  const handleRun = () => {
    if (mode === "latest") {
      onRunLatest(sheet);
    } else if (canRunUpload) {
      onRunUpload(sheet, eccFile, s4File);
    }
  };

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="flex-1 min-w-[240px]">
          <h3 className="text-sm font-medium text-gray-900">
            Validate ECC → S/4 conversion
          </h3>
          <p className="text-xs text-gray-500 mt-1 max-w-md">
            Runs the mapping-derived rules (field coverage, mandatory
            completeness, type &amp; length, record count, key integrity, and
            value transformation accuracy) against the selected sheet.
          </p>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Sheet</label>
          <div className="flex rounded-md border border-gray-300 overflow-hidden">
            {SHEETS.map((s) => (
              <button
                key={s.value}
                type="button"
                onClick={() => setSheet(s.value)}
                className={`px-3 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
                  sheet === s.value
                    ? "bg-slate-900 text-white"
                    : "bg-white text-gray-600 hover:bg-gray-50"
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-end gap-6 border-t border-gray-100 pt-5">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-gray-500">Source</label>
          <div className="flex rounded-md border border-gray-300 overflow-hidden">
            <button
              type="button"
              onClick={() => setMode("latest")}
              className={`px-3 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
                mode === "latest"
                  ? "bg-slate-900 text-white"
                  : "bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              Latest files
            </button>
            <button
              type="button"
              onClick={() => setMode("upload")}
              className={`px-3 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
                mode === "upload"
                  ? "bg-slate-900 text-white"
                  : "bg-white text-gray-600 hover:bg-gray-50"
              }`}
            >
              Upload files
            </button>
          </div>
        </div>

        {mode === "latest" ? (
          <p className="text-xs text-gray-500 max-w-sm">
            Auto-loads the newest ECC extract and the newest fetched S/4 file
            for {sheet} from the server.
          </p>
        ) : (
          <div className="flex flex-wrap gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-500">
                ECC extract
              </label>
              <input
                type="file"
                accept=".csv,.xlsx,.xls"
                onChange={(e) => setEccFile(e.target.files?.[0] || null)}
                className="text-xs text-gray-600 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-500">
                S/4 file
              </label>
              <input
                type="file"
                accept=".csv,.xlsx,.xls"
                onChange={(e) => setS4File(e.target.files?.[0] || null)}
                className="text-xs text-gray-600 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-medium file:bg-gray-100 file:text-gray-700 hover:file:bg-gray-200"
              />
            </div>
          </div>
        )}

        <button
          onClick={handleRun}
          disabled={isLoading || (mode === "upload" && !canRunUpload)}
          className="ml-auto bg-slate-900 hover:bg-slate-800 disabled:bg-slate-400 disabled:cursor-not-allowed text-white text-sm font-medium px-5 py-2.5 rounded-md transition-colors whitespace-nowrap"
        >
          {isLoading ? "Validating…" : "Run Validation"}
        </button>
      </div>
    </div>
  );
}

export default ValidationActionCard;
