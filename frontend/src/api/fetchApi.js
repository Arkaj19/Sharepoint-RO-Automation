// const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

// /**
//  * Triggers the backend to pull the SharePoint extracts and combine them.
//  * Returns { total_files_fetched, combined_files: [{name, source_files, row_count, saved_path}], message }
//  */
// export async function fetchFromSharePoint() {
//   const res = await fetch(`${API_BASE}/api/fetch`, { method: "POST" });

//   if (!res.ok) {
//     const body = await res.json().catch(() => ({}));
//     throw new Error(body.detail || `Request failed (${res.status})`);
//   }

//   return res.json();
// }

// /**
//  * Downloads the combined file as .xlsx. The backend converts it from the
//  * stored .csv on the fly, so this triggers a real (small) delay only at
//  * download time, not during fetch.
//  */
// export async function downloadCombinedFile(name) {
//   const res = await fetch(`${API_BASE}/api/download/${name}`);

//   if (!res.ok) {
//     const body = await res.json().catch(() => ({}));
//     throw new Error(body.detail || `Request failed (${res.status})`);
//   }

//   const blob = await res.blob();
//   const url = window.URL.createObjectURL(blob);
//   const link = document.createElement("a");
//   link.href = url;
//   link.download = `${name}.xlsx`;
//   document.body.appendChild(link);
//   link.click();
//   link.remove();
//   window.URL.revokeObjectURL(url);
// }

// /**
//  * Fetches the first `limit` rows of an already-combined file.
//  * Returns { name, columns, rows, total_rows, preview_row_count }
//  */
// export async function fetchPreview(name, limit = 20) {
//   const res = await fetch(`${API_BASE}/api/preview/${name}?limit=${limit}`);

//   if (!res.ok) {
//     const body = await res.json().catch(() => ({}));
//     throw new Error(body.detail || `Request failed (${res.status})`);
//   }

//   return res.json();
// }

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

/**
 * Triggers the backend to pull the S4 (Databricks) extracts and combine them.
 * Returns { total_files_fetched, combined_files: [...], message }
 */
export async function fetchS4Files() {
  const res = await fetch(`${API_BASE}/api/fetch/s4`, { method: "POST" });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  return res.json();
}

/**
 * Triggers the backend to pull the ECC (DAP) extracts from SharePoint root
 * and combine them.
 * Returns { total_files_fetched, combined_files: [...], message }
 */
export async function fetchEccFiles() {
  const res = await fetch(`${API_BASE}/api/fetch/ecc`, { method: "POST" });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  return res.json();
}

/**
 * Downloads the combined file as .xlsx. The backend converts it from the
 * stored .csv on the fly, so this triggers a real (small) delay only at
 * download time, not during fetch.
 *
 * @param {string} family      - "MARC" or "MBEW"
 * @param {string} sourceType  - "S4" or "ECC"
 */
export async function downloadCombinedFile(family, sourceType) {
  const res = await fetch(`${API_BASE}/api/download/${family}/${sourceType}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${family}_${sourceType}_combined.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

/**
 * Fetches the first `limit` rows of an already-combined file.
 * Returns { name, columns, rows, total_rows, preview_row_count }
 *
 * @param {string} family      - "MARC" or "MBEW"
 * @param {string} sourceType  - "S4" or "ECC"
 * @param {number} limit       - rows to preview (1–200)
 */
export async function fetchPreview(family, sourceType, limit = 20) {
  const res = await fetch(
    `${API_BASE}/api/preview/${family}/${sourceType}?limit=${limit}`
  );

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  return res.json();
}