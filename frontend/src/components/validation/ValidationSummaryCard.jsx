const STATUS_STYLES = {
  pass: { label: "Passed", badge: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  fail: { label: "Failed", badge: "bg-red-50 text-red-700 border-red-200" },
  warning: { label: "Warning", badge: "bg-amber-50 text-amber-700 border-amber-200" },
};

function ValidationSummaryCard({ report }) {
  const style = STATUS_STYLES[report.overall_status] || STATUS_STYLES.warning;
  const passCount = report.rules.filter((r) => r.status === "pass").length;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h3 className="text-sm font-medium text-gray-900">
            {report.sheet} validation report
          </h3>
          <p className="text-xs text-gray-500 mt-1">
            Key: {report.key_fields.join(" + ")}
          </p>
        </div>
        <span
          className={`text-xs font-medium px-3 py-1 rounded-full border ${style.badge}`}
        >
          {style.label} · {passCount}/{report.rules.length} rules passed
        </span>
      </div>

      <dl className="grid grid-cols-1 gap-4 text-sm mt-5">
        <div>
          <dt className="text-gray-500 text-xs">Sources</dt>
          <dd className="text-gray-700 text-xs mt-1 truncate" title={report.ecc_source}>
            ECC source file: {report.ecc_source}
          </dd>
          <dd className="text-gray-700 text-xs truncate" title={report.s4_source}>
            Actual S/4 file: {report.s4_source}
          </dd>
        </div>
      </dl>
    </div>
  );
}

export default ValidationSummaryCard;