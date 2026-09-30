import { apiGet, apiPost, apiText, downloadFile } from "./client";

// -- refresh & snapshots -------------------------------------------------------
export const startRefresh = (force = false) => apiPost("/api/refresh", { force });
export const getRefreshRun = (id) => apiGet(`/api/refresh/${id}`);
export const listRefreshRuns = (limit = 10) => apiGet(`/api/refresh?limit=${limit}`);
export const listSnapshots = () => apiGet("/api/snapshots");
export const previewSnapshot = (obj, side, version, limit = 20) =>
  apiGet(`/api/snapshots/${obj}/${side}/${version}/preview?limit=${limit}`);
export const downloadSnapshot = (obj, side, version) =>
  downloadFile(`/api/snapshots/${obj}/${side}/${version}/download`, `${obj}_${side}_${version}.xlsx`);

// -- change sets -------------------------------------------------------------------
export const listChangesets = (obj, limit = 20) => apiGet(`/api/changesets?object=${obj}&limit=${limit}`);
export const getChangeset = (id) => apiGet(`/api/changesets/${id}`);

// -- proposals & agent ---------------------------------------------------------------
export const listProposals = (obj, status) =>
  apiGet(`/api/proposals?object=${obj}${status ? `&status=${status}` : ""}`);
export const getProposal = (id) => apiGet(`/api/proposals/${id}`);
export const decideOps = (id, decisions, decidedBy) =>
  apiPost(`/api/proposals/${id}/decisions`, { decisions, decided_by: decidedBy || null });
export const applyProposal = (id, appliedBy) =>
  apiPost(`/api/proposals/${id}/apply`, { applied_by: appliedBy || null });
export const rerunAgent = (obj) => apiPost("/api/agent/run", { object: obj });
export const listAgentRuns = (obj, limit = 10) => apiGet(`/api/agent/runs?object=${obj}&limit=${limit}`);

// -- mappings --------------------------------------------------------------------------
export const listMappings = () => apiGet("/api/mappings");
export const getMapping = (obj) => apiGet(`/api/mappings/${obj}`);
export const getMappingYaml = (obj, version) =>
  apiText(`/api/mappings/${obj}/yaml${version ? `?version=${version}` : ""}`);
export const listMappingVersions = (obj) => apiGet(`/api/mappings/${obj}/versions`);
export const getMappingDiff = (obj, fromVersion, toVersion) =>
  apiText(`/api/mappings/${obj}/diff?from_version=${fromVersion}${toVersion ? `&to_version=${toVersion}` : ""}`);
export const getChangelog = (obj) => apiGet(`/api/mappings/${obj}/changelog`);

// -- rule book --------------------------------------------------------------------------
export const generateRuleBook = (includeDrafts) => apiPost("/api/rulebook", { include_drafts: includeDrafts });
export const listRuleBooks = () => apiGet("/api/rulebook");
export const downloadRuleBook = (name) => downloadFile(`/api/rulebook/${name}/download`, name);

// -- reference logic import & own-logic generation ----------------------------------------------------
export const importLogic = (obj) => apiPost(`/api/import/${obj}`, {});
export const listImports = (obj) => apiGet(`/api/import?object=${obj}&limit=5`);
export const generateOutput = (obj) => apiPost(`/api/outputs/${obj}/generate`, {});
export const listOutputs = (obj, limit = 10) => apiGet(`/api/outputs?object=${obj}&limit=${limit}`);
export const getOutput = (obj, runId) => apiGet(`/api/outputs/${obj}/${runId}`);
export const downloadOutput = (obj, runId) =>
  downloadFile(`/api/outputs/${obj}/${runId}/download`, `S_${obj}_${runId}.csv`);
