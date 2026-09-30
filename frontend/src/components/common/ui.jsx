import { useState } from "react";

export const OBJECTS = [
  { value: "MARC", label: "MARC (Plant Data)" },
  { value: "MBEW", label: "MBEW (Valuation Data)" },
];

export function PageHeader({ title, subtitle, children }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900">{title}</h2>
        {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-3">{children}</div>}
    </div>
  );
}

export function Card({ title, subtitle, actions, children, className = "" }) {
  return (
    <div className={`bg-white border border-gray-200 rounded-lg p-5 ${className}`}>
      {(title || actions) && (
        <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
          <div>
            {title && <h3 className="text-sm font-semibold text-gray-900">{title}</h3>}
            {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function Button({ children, variant = "primary", size = "md", className = "", ...props }) {
  const base = "font-medium rounded-md transition-colors whitespace-nowrap disabled:cursor-not-allowed";
  const sizes = { sm: "text-xs px-3 py-1.5", md: "text-sm px-4 py-2" };
  const variants = {
    primary: "bg-slate-900 hover:bg-slate-800 disabled:bg-slate-400 text-white",
    secondary: "bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:text-gray-400",
    accept: "bg-emerald-600 hover:bg-emerald-700 disabled:bg-emerald-300 text-white",
    reject: "bg-white border border-red-300 text-red-700 hover:bg-red-50 disabled:text-red-300",
  };
  return (
    <button className={`${base} ${sizes[size]} ${variants[variant]} ${className}`} {...props}>
      {children}
    </button>
  );
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="flex rounded-md border border-gray-300 overflow-hidden">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={`px-3 py-2 text-xs font-medium whitespace-nowrap transition-colors ${
            value === o.value ? "bg-slate-900 text-white" : "bg-white text-gray-600 hover:bg-gray-50"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

const TONES = {
  gray: "bg-gray-50 text-gray-700 border-gray-200",
  green: "bg-emerald-50 text-emerald-700 border-emerald-200",
  red: "bg-red-50 text-red-700 border-red-200",
  amber: "bg-amber-50 text-amber-700 border-amber-200",
  blue: "bg-sky-50 text-sky-700 border-sky-200",
  violet: "bg-violet-50 text-violet-700 border-violet-200",
};

export function Badge({ tone = "gray", children, title }) {
  return (
    <span title={title} className={`inline-flex items-center text-[11px] font-medium px-2 py-0.5 rounded-full border ${TONES[tone]}`}>
      {children}
    </span>
  );
}

const STATUS_TONES = {
  pending: "gray", accepted: "green", rejected: "red", applied: "blue", failed: "red", stale: "amber",
  open: "amber", closed: "gray", superseded: "gray", completed: "green", running: "blue", queued: "gray",
  no_changes: "gray", changed: "blue", unchanged: "gray", kept: "amber", missing: "gray", error: "red", baseline: "violet",
  approved: "green", migrated: "gray", high: "red", medium: "amber", low: "gray",
};

export function StatusBadge({ status }) {
  return <Badge tone={STATUS_TONES[status] || "gray"}>{String(status).replace("_", " ")}</Badge>;
}

export function ConfidenceBadge({ value }) {
  if (value === null || value === undefined) return null;
  const tone = value >= 0.9 ? "green" : value >= 0.7 ? "amber" : "red";
  return <Badge tone={tone} title="Agent confidence">{Math.round(value * 100)}%</Badge>;
}

export function ErrorBox({ error }) {
  if (!error) return null;
  return (
    <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3 mb-4">{error}</div>
  );
}

export function Empty({ children }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg px-6 py-10 text-center text-sm text-gray-500">
      {children}
    </div>
  );
}

export function JsonView({ value, label = "Evidence" }) {
  const [open, setOpen] = useState(false);
  if (value === null || value === undefined || (typeof value === "object" && Object.keys(value).length === 0))
    return null;
  return (
    <div className="mt-2">
      <button onClick={() => setOpen((o) => !o)} className="text-xs font-medium text-slate-700 hover:underline">
        {open ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
      </button>
      {open && (
        <pre className="mt-2 bg-gray-50 border border-gray-200 rounded p-3 text-[11px] leading-snug overflow-auto max-h-72">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function Stat({ label, value, tone }) {
  const color = tone === "red" ? "text-red-700" : tone === "amber" ? "text-amber-700" : "text-gray-900";
  return (
    <div>
      <dt className="text-gray-500 text-xs">{label}</dt>
      <dd className={`font-medium text-lg ${color}`}>{value ?? "—"}</dd>
    </div>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="flex gap-1 border-b border-gray-200 mb-4 overflow-x-auto">
      {tabs.map((t) => (
        <button
          key={t.value}
          onClick={() => onChange(t.value)}
          className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap ${
            value === t.value ? "border-black text-black" : "border-transparent text-gray-500 hover:text-black"
          }`}
        >
          {t.label}
          {t.count !== undefined && <span className="ml-1.5 text-xs text-gray-400">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function useReviewer() {
  const [name, setName] = useState(() => {
    try {
      return window.localStorage.getItem("reviewer") || "";
    } catch {
      return "";
    }
  });
  const update = (v) => {
    setName(v);
    try {
      window.localStorage.setItem("reviewer", v);
    } catch {
      /* storage unavailable - keep it in memory */
    }
  };
  return [name, update];
}
