import { useState } from "react";
import RuleDetailValue from "./RuleDetailValue";

const STATUS_META = {
  pass: { icon: "✓", classes: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  fail: { icon: "✕", classes: "bg-red-50 text-red-700 border-red-200" },
  warning: { icon: "!", classes: "bg-amber-50 text-amber-700 border-amber-200" },
};

function RuleResultCard({ rule }) {
  const [expanded, setExpanded] = useState(false);
  const meta = STATUS_META[rule.status] || STATUS_META.warning;
  const hasDetails = rule.details && rule.details.length > 0;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5">
      <div className="flex items-start gap-3">
        <span
          className={`flex-shrink-0 h-6 w-6 rounded-full border flex items-center justify-center text-xs font-semibold ${meta.classes}`}
        >
          {meta.icon}
        </span>
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-semibold text-gray-900">{rule.name}</h4>
          <p className="text-xs text-gray-600 mt-1">{rule.summary}</p>
        </div>
        {hasDetails && (
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-xs font-medium text-slate-900 hover:underline whitespace-nowrap"
          >
            {expanded ? "Hide details" : "Show details"}
          </button>
        )}
      </div>

      {expanded && hasDetails && (
        <div className="mt-4 pl-9 border-t border-gray-100 pt-4 text-xs space-y-2 max-h-80 overflow-auto">
          {rule.details.map((detail, i) => (
            <div key={i}>
              <RuleDetailValue value={detail} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default RuleResultCard;
