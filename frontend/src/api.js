async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  if (response.status === 204) return {};
  if (!contentType.includes("application/json")) {
    const text = await response.text().catch(() => "");
    if (!response.ok) {
      throw new Error(text.slice(0, 200) || `请求失败 (${response.status})`);
    }
    throw new Error(text.trimStart().startsWith("<!doctype") ? "接口未就绪，请重启服务后重试" : "服务返回了非 JSON 数据");
  }
  const payload = await response.json().catch(() => null);
  if (payload === null) throw new Error("服务返回了无法解析的数据");
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `请求失败 (${response.status})`);
  }
  return payload;
}

export function getStats() {
  return request("/api/stats");
}

export function getDashboardOverview(days = 30, signal) {
  return request(`/api/dashboard/overview?days=${encodeURIComponent(days)}`, { signal });
}

export function getConfig() {
  return request("/api/config");
}

export function getIngestionConfig() {
  return request("/api/v1/settings/ingestion");
}

export function saveIngestionConfig(apiKey) {
  return request("/api/v1/settings/ingestion", { method: "POST", body: JSON.stringify({ api_key: apiKey }) });
}

export function getImports() {
  return request("/api/imports?limit=100");
}

export function getIngestionRecords(status, query) {
  const filters = [];
  if (status && status !== "all") filters.push(`status=${encodeURIComponent(status)}`);
  if (query) filters.push(`q=${encodeURIComponent(query)}`);
  const suffix = filters.length ? `&${filters.join("&")}` : "";
  return request(`/api/ingestion/records?limit=100${suffix}`);
}

export function getIngestionRecord(ingestionId) {
  return request(`/api/ingestion/records/${encodeURIComponent(ingestionId)}`);
}

export function reprocessIgnoredIngestion(limit = 100) {
  return request("/api/ingestion/reprocess", { method: "POST", body: JSON.stringify({ limit }) });
}

export function retryIngestionRecord(ingestionId) {
  return request(`/api/ingestion/records/${encodeURIComponent(ingestionId)}/retry`, { method: "POST", body: "{}" });
}

export function retryFailedIngestions(limit = 100) {
  return request("/api/ingestion/retry-failed", { method: "POST", body: JSON.stringify({ limit }) });
}

export function getMediaFilters() {
  return request("/api/media/filters");
}

export function getMedia(params, signal) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  });
  return request(`/api/media?${query.toString()}`, { signal });
}

export function getMediaDetail(mediaId) {
  return request(`/api/media/${mediaId}`);
}



export function getResources(params, signal) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  });
  return request(`/api/resources?${query.toString()}`, { signal });
}

export function getResourceGroup(groupId, params, signal) {
  const query = new URLSearchParams({ group_id: groupId });
  Object.entries(params).forEach(([key, value]) => {
    if (key === "include_resources" || key === "page" || key === "page_size") return;
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  });
  return request(`/api/resources/group?${query.toString()}`, { signal });
}

export function getResourceFilters() {
  return request("/api/resources/filters");
}

export function getResourceDetail(resourceId) {
  return request(`/api/resources/${resourceId}`);
}

export function updateResource(resourceId, values) {
  return request(`/api/resources/${resourceId}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function checkResource(resourceId) {
  return request(`/api/resources/${resourceId}/check`, { method: "POST", body: "{}" });
}

export function updateMedia(mediaId, values) {
  return request(`/api/media/${mediaId}`, {
    method: "PATCH",
    body: JSON.stringify(values),
  });
}

export function importText(name, content, kind = "auto") {
  return request("/api/import", {
    method: "POST",
    body: JSON.stringify({ name, content, kind }),
  });
}

export function searchCandidates(mediaId) {
  return request(`/api/media/${mediaId}/candidates`, {
    method: "POST",
    body: JSON.stringify({ auto_link: false, refresh_share: true }),
  });
}

export function searchTmdb(params, signal) {
  const query = new URLSearchParams(params);
  return request(`/api/tmdb/search?${query}`, { signal });
}

export function linkCandidate(mediaId, candidate) {
  return request(`/api/media/${mediaId}/tmdb/link`, {
    method: "POST",
    body: JSON.stringify({
      tmdb_id: candidate.tmdb_id,
      media_type: candidate.media_type,
      confidence: candidate.score ?? 1,
      method: "manual",
    }),
  });
}

export function rejectCandidate(mediaId, candidateId) {
  return request(`/api/media/${mediaId}/candidates/${candidateId}/reject`, {
    method: "POST",
    body: "{}",
  });
}

export function unlinkTmdb(mediaId) {
  return request(`/api/media/${mediaId}/tmdb/unlink`, {
    method: "POST",
    body: "{}",
  });
}

export function syncPending(limit = 20) {
  return request("/api/tmdb/sync", {
    method: "POST",
    body: JSON.stringify({ limit }),
  });
}
