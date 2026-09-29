const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function handleResponse(res) {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

/**
* Auto-loads the latest ECC extract and S/4 combined file for `sheet`
* ("MARC" | "MBEW") and runs the mapping-derived validation rules.
* Returns a ValidationReportResponse.
*/
export async function validateLatest(sheet) {
  const res = await fetch(`${API_BASE}/api/validate/latest?sheet=${sheet}`);
  return handleResponse(res);
}

/**
* Same validation, but against a directly uploaded ECC file + S/4 file
* (useful before the ECC_DATA/S4 folders are wired up end to end).
*/
export async function validateUpload(sheet, eccFile, s4File) {
  const form = new FormData();
  form.append("ecc_file", eccFile);
  form.append("s4_file", s4File);

  const res = await fetch(`${API_BASE}/api/validate/upload?sheet=${sheet}`, {
    method: "POST",
    body: form,
  });
  return handleResponse(res);
}