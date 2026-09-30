import { API_BASE, apiGet } from "./client";

/**
 * Validates the current ECC and S/4 snapshot versions of `sheet`
 * ("MARC" | "MBEW") against its YAML mapping.
 * Returns a ValidationReportResponse.
 */
export function validateLatest(sheet) {
  return apiGet(`/api/validate/latest?sheet=${sheet}`);
}

/**
 * Same validation, but against a directly uploaded ECC file + S/4 file.
 */
export async function validateUpload(sheet, eccFile, s4File) {
  const form = new FormData();
  form.append("ecc_file", eccFile);
  form.append("s4_file", s4File);

  const res = await fetch(`${API_BASE}/api/validate/upload?sheet=${sheet}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}
