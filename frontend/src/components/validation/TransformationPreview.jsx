import { useEffect, useState } from "react";
import { fetchTransformationPreview } from "../../api/previewApi";

/**
 * Transformation Preview  –  one Excel-style table
 *
 *   MATNR | Type | ECC Plant | S/4 Plant | [ECC | S/4] per field | LOSGR conversion | Rules applied
 *
 * Usage:
 *   <TransformationPreview tableType="MARC" matnrs={["7079800107", "7079800120"]} />
 */
export default function TransformationPreview({
  tableType = "MARC",
  limit = 50,
  matnrs = [],
  source = "live",
}) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const matnrKey = matnrs.join(",");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchTransformationPreview(tableType, limit, { matnrs, source })
      .then((d) => !cancelled && (setData(d), setError(null)))
      .catch((e) => !cancelled && (setError(e.message), setData(null)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableType, limit, matnrKey, source]);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-3 py-16 text-gray-500 text-sm">
        <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
        Loading {tableType} transformation…
      </div>
    );
  }
  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 text-red-800 rounded-lg p-5 text-sm">
        <strong>Error loading preview:</strong> {error}
      </div>
    );
  }
  if (!data || !data.rows || data.rows.length === 0) {
    return (
      <div className="bg-white rounded-lg p-8 text-center text-gray-500 text-sm">
        <p>No S/4 rows were produced for the selected records.</p>
        <Reasons data={data} />
      </div>
    );
  }

  const fields = data.pair_fields || [];

  return (
    <div className="max-w-full">
      {/* Title + legend */}
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">
            {tableType} – ECC → S/4 Transformation
            {data._fromSnapshot && (
              <span className="ml-2 align-middle text-[11px] font-medium bg-gray-200 text-gray-700 px-2 py-0.5 rounded">
                Snapshot data
              </span>
            )}
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {data.ecc_rows_in} ECC rows → {data.rows.length} S/4 rows
          </p>
          <Reasons data={data} />
        </div>
        <div className="flex flex-wrap gap-2 text-[11px] text-gray-700">
          <Legend cls="bg-blue-50 border-blue-200" label="ECC value" />
          <Legend cls="bg-green-50 border-green-200" label="S/4 value" />
          <Legend cls="bg-amber-200 border-amber-300" label="Changed by rule" />
        </div>
      </div>

      {/* The table */}
      <div className="overflow-auto max-h-[75vh] border border-gray-300 rounded-lg bg-white">
        <table className="text-xs border-collapse font-mono min-w-full">
          <thead className="sticky top-0 z-10">
            <tr className="bg-[#1F3864] text-white">
              <Th colSpan={4}>Key</Th>
              {fields.map((f) => (
                <Th key={f} colSpan={2}>{f}</Th>
              ))}
              <Th colSpan={5}>LOSGR</Th>
              <Th rowSpan={2} className="min-w-[380px]">Rules applied</Th>
            </tr>
            <tr className="bg-[#1F3864] text-white">
              <Th>MATNR</Th>
              <Th>Type</Th>
              <Th>ECC Plant</Th>
              <Th>WERKS</Th>
              {fields.map((f) => (
                <FragmentHeader key={f} />
              ))}
              <Th>Conversion</Th>
              <Th>ECC</Th>
              <Th>Calc ECC</Th>
              <Th>S/4</Th>
              <Th>S/4 vs ECC</Th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => {
              const calc = r.ecc_losgr == null ? null : round(Number(r.ecc_losgr) * r.conversion);
              const ok = round(calc ?? 0) === round(Number(r.s4_losgr ?? 0));
              const losgrChanged = String(calc) !== String(r.ecc_losgr);
              return (
                <tr key={r.record_id} className="hover:bg-gray-50">
                  <Td>{r.matnr}</Td>
                  <Td>{r.type}</Td>
                  <Td>{r.ecc_plant}</Td>
                  <Td className="font-semibold">{r.s4_plant}</Td>

                  {fields.map((f) => {
                    const p = r.pairs[f];
                    return (
                      <PairCells key={f} pair={p} />
                    );
                  })}

                  <Td className="text-right">{r.conversion}</Td>
                  <Td className="bg-blue-50 text-right">{r.ecc_losgr ?? ""}</Td>
                  <Td className="text-right">{calc ?? ""}</Td>
                  <Td className={`text-right ${losgrChanged ? "bg-amber-200 font-semibold" : "bg-green-50"}`}>
                    {r.s4_losgr ?? ""}
                  </Td>
                  <Td className={`text-center font-semibold ${ok ? "text-green-700" : "text-red-600"}`}>
                    {ok ? "TRUE" : "FALSE"}
                  </Td>

                  <Td className="font-sans text-gray-700 whitespace-normal">{r.rules}</Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// -- helpers ---------------------------------------------------------------

// Why some ECC rows produced no S/4 row (grouped), plus requested MATNRs missing from the ECC file
function Reasons({ data }) {
  const groups = {};
  (data?.unmapped_ecc_rows || []).forEach((u) => {
    groups[u.reason || "No S/4 output"] = (groups[u.reason || "No S/4 output"] || 0) + 1;
  });
  const missing = data?.matnrs_not_in_ecc || [];
  if (!Object.keys(groups).length && !missing.length) return null;
  return (
    <ul className="mt-2 text-xs text-gray-500 space-y-0.5 text-left inline-block">
      {Object.entries(groups).map(([reason, n]) => (
        <li key={reason}>• {n} ECC row(s): {reason}</li>
      ))}
      {missing.length > 0 && <li>• Not found in ECC file: {missing.join(", ")}</li>}
    </ul>
  );
}

const round = (n) => (Number.isFinite(n) ? parseFloat(n.toFixed(6)) : n);

function Legend({ cls, label }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`inline-block w-3 h-3 rounded-sm border ${cls}`} />
      {label}
    </span>
  );
}

function Th({ children, className = "", ...rest }) {
  return (
    <th
      {...rest}
      className={`px-2 py-1.5 border border-[#2F4A7F] text-center font-semibold whitespace-nowrap ${className}`}
    >
      {children}
    </th>
  );
}

// "ECC" | "S/4" sub-headers for one field pair
function FragmentHeader() {
  return (
    <>
      <Th>ECC</Th>
      <Th>S/4</Th>
    </>
  );
}

function Td({ children, className = "" }) {
  return (
    <td className={`px-2 py-1 border border-gray-200 whitespace-nowrap ${className}`}>{children}</td>
  );
}

function PairCells({ pair }) {
  const s4Cls = pair.changed ? "bg-amber-200 font-semibold" : "bg-green-50";
  return (
    <>
      <Td className="bg-blue-50">{pair.ecc ?? ""}</Td>
      <Td className={s4Cls}>{pair.s4 ?? ""}</Td>
    </>
  );
}