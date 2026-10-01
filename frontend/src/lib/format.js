export function formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

export function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index >= 3 ? 2 : 1)} ${units[index]}`;
}

export function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function posterUrl(path, size = "w342") {
  return path ? `https://image.tmdb.org/t/p/${size}${path}` : "/fallback-poster.png";
}

export function displayMediaType(value) {
  if (value === "movie") return "电影";
  if (value === "tv") return "剧集";
  return "待判断";
}

export function displayStatus(value) {
  const labels = {
    matched: "已匹配",
    review: "待确认",
    not_found: "未找到",
    error: "匹配错误",
    pending: "待匹配",
  };
  return labels[value] || "待匹配";
}

export function maskedSource(value) {
  if (!value) return "";
  if (value.startsWith("ed2k://")) return value;
  return value.replace(/([?&]password=)[^&#\s]+/i, "$1••••");
}

export function qualityList(value) {
  if (!value) return [];
  return value.split(",").filter(Boolean).slice(0, 3);
}
