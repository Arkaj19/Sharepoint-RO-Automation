function formatKey(key) {
  return key
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// Renders one heterogeneous detail entry (shape differs per rule: a missing
// field, a sample of key mismatches, a nested list of value mismatches...).
// Recurses one or two levels deep, which is as deep as the backend ever nests.
function RuleDetailValue({ value }) {
  if (value === null || value === undefined) {
    return <span className="text-gray-400">—</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-gray-400">none</span>;
    return (
      <ul className="space-y-1.5">
        {value.map((item, i) => (
          <li key={i} className="bg-gray-50 rounded px-2 py-1.5">
            <RuleDetailValue value={item} />
          </li>
        ))}
      </ul>
    );
  }

  if (typeof value === "object") {
    return (
      <dl className="grid grid-cols-[max-content,1fr] gap-x-3 gap-y-1">
        {Object.entries(value).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-gray-500 whitespace-nowrap">{formatKey(k)}:</dt>
            <dd className="text-gray-800">
              <RuleDetailValue value={v} />
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return <span>{String(value)}</span>;
}

export default RuleDetailValue;
