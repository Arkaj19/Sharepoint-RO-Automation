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
 * Triggers the backend to pull the SharePoint extracts and combine them.
 * Returns { total_files_fetched, combined_files: [{name, source_files, row_count, saved_path}], message }
 */
export async function fetchFromSharePoint() {
  const res = await fetch(`${API_BASE}/api/fetch`, { method: "POST" });

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
 */
export async function downloadCombinedFile(name) {
  const res = await fetch(`${API_BASE}/api/download/${name}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${name}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

/**
 * Fetches the first `limit` rows of an already-combined file.
 * Returns { name, columns, rows, total_rows, preview_row_count }
 */
export async function fetchPreview(name, limit = 20) {
  const res = await fetch(`${API_BASE}/api/preview/${name}?limit=${limit}`);

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }

  return res.json();
}