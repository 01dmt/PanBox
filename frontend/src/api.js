async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || payload.detail || `请求失败 (${response.status})`);
  }
  return payload;
}

export function getStats() {
  return request("/api/stats");
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

export function getShareAudit() {
  return request("/api/share-audit");
}

export function pauseShareAudit() {
  return request("/api/share-audit/pause", { method: "POST", body: "{}" });
}

export function getImports() {
  return request("/api/imports?limit=100");
}

export function getIngestionRecords() {
  return request("/api/ingestion/records?limit=100");
}

export function getIngestionRecord(ingestionId) {
  return request(`/api/ingestion/records/${encodeURIComponent(ingestionId)}`);
}

export function reprocessIgnoredIngestion(limit = 100) {
  return request("/api/ingestion/reprocess", { method: "POST", body: JSON.stringify({ limit }) });
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
