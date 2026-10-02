const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function handleResponse(res) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function loadSnapshot(tableType) {
  return handleResponse(await fetch(`/demo/${tableType.toLowerCase()}_preview.json`));
}

/**
 * Transformation preview (MARC).
 *
 * @param {"MARC"} tableType
 * @param {number} limit            used only when no matnrs are given
 * @param {object} opts
 * @param {string[]} opts.matnrs    restrict to these materials
 * @param {"live"|"snapshot"} opts.source
 */
export async function fetchTransformationPreview(
  tableType,
  limit = 50,
  { matnrs = [], source = "live" } = {}
) {
  if (source === "snapshot") return loadSnapshot(tableType);

  const qs = new URLSearchParams({ limit });
  matnrs.forEach((m) => qs.append("matnr", m));

  try {
    const res = await fetch(`${API_BASE}/api/preview/transformation/${tableType}?${qs}`);
    return await handleResponse(res);
  } catch (err) {
    console.warn("Live preview failed, trying snapshot:", err.message);
    try {
      return { ...(await loadSnapshot(tableType)), _fromSnapshot: true };
    } catch {
      throw err; // no snapshot available: show the real error
    }
  }
}