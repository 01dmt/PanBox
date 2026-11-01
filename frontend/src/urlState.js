const MEDIA_FILTER_KEYS = [
  "status",
  "type",
  "category",
  "resource_kind",
  "source",
  "availability",
  "year",
  "genre",
  "country",
  "quality",
  "codec",
  "hdr",
  "sort",
  "page",
  "pageSize",
];

const RESOURCE_FILTER_KEYS = [
  "group",
  "resource_class",
  "provider",
  "availability",
  "tmdb_status",
  "year",
  "tag",
  "quality",
  "channel",
  "updated",
  "sort",
  "page",
  "pageSize",
];

const MEDIA_DEFAULTS = Object.freeze({
  status: "all",
  type: "movie",
  category: "all",
  resource_kind: "all",
  source: "all",
  availability: "available",
  year: "all",
  genre: "all",
  country: "all",
  quality: "all",
  codec: "all",
  hdr: "all",
  sort: "updated_desc",
  page: 1,
  pageSize: 36,
});

export const RESOURCE_DEFAULTS = Object.freeze({
  group: "all",
  resource_class: "all",
  provider: "all",
  availability: "all",
  tmdb_status: "all",
  year: "all",
  tag: "all",
  quality: "all",
  channel: "all",
  updated: "all",
  sort: "updated_desc",
  page: 1,
  pageSize: 30,
});

export function isCatalogView(view) {
  return ["library", "movies", "tv"].includes(view);
}

export function defaultMediaFilters(view) {
  return {
    ...MEDIA_DEFAULTS,
    type: view === "movies" ? "movie" : view === "tv" ? "tv" : "all",
    availability: isCatalogView(view) ? "available" : "all",
    pageSize: view === "pending" ? 20 : 36,
  };
}

function positiveInt(value, fallback, max = 10000) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed) || parsed < 1) return fallback;
  return Math.min(parsed, max);
}

function readString(params, key, fallback) {
  const value = params.get(key);
  return value == null || value === "" ? fallback : value;
}

function readPageSize(params, fallback, allowed) {
  const parsed = positiveInt(params.get("pageSize"), fallback, 100);
  return allowed.includes(parsed) ? parsed : fallback;
}

export function readMediaFilters(params, view) {
  const defaults = defaultMediaFilters(view);
  const filters = { ...defaults };
  const pageSizes = view === "pending" ? [20, 30, 50, 100] : [24, 36, 48, 60];
  for (const key of MEDIA_FILTER_KEYS) {
    if (key === "page") {
      filters.page = positiveInt(params.get(key), defaults.page);
    } else if (key === "pageSize") {
      filters.pageSize = readPageSize(params, defaults.pageSize, pageSizes);
    } else {
      filters[key] = readString(params, key, defaults[key]);
    }
  }
  return filters;
}

export function readResourceFilters(params) {
  const filters = { ...RESOURCE_DEFAULTS };
  for (const key of RESOURCE_FILTER_KEYS) {
    if (key === "page") {
      filters.page = positiveInt(params.get(key), RESOURCE_DEFAULTS.page);
    } else if (key === "pageSize") {
      filters.pageSize = readPageSize(params, RESOURCE_DEFAULTS.pageSize, [30, 50, 100]);
    } else {
      filters[key] = readString(params, key, RESOURCE_DEFAULTS[key]);
    }
  }
  return filters;
}

function putNonDefault(next, filters, defaults, keys) {
  for (const key of keys) {
    const value = filters[key];
    if (value == null || String(value) === String(defaults[key])) continue;
    next.set(key, String(value));
  }
}

export function withMediaFilters(params, filters, view) {
  const next = new URLSearchParams(params);
  MEDIA_FILTER_KEYS.forEach((key) => next.delete(key));
  // 筛选条件变化后，原来的详情 ID 可能已不在结果集中。
  next.delete("media");
  putNonDefault(next, filters, defaultMediaFilters(view), MEDIA_FILTER_KEYS);
  return next;
}

export function withResourceFilters(params, filters) {
  const next = new URLSearchParams(params);
  RESOURCE_FILTER_KEYS.forEach((key) => next.delete(key));
  // 分组详情和资源详情都依赖当前筛选结果，筛选变化后一并退出。
  next.delete("group_id");
  next.delete("resource");
  putNonDefault(next, filters, RESOURCE_DEFAULTS, RESOURCE_FILTER_KEYS);
  return next;
}

export function readSelectionId(params, key) {
  const parsed = Number.parseInt(params.get(key) ?? "", 10);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}
