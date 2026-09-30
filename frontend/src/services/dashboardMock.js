// Illustrative fixtures only. Never interpreted as live crawler or transfer activity.
export const dashboardMock = {
  mode: "mock",
  ingestion: { configured: false, transport: null, endpoint: null, paused: null, last_received_at: null, received_today: null, parsed_rate: null, discarded_today: null, duplicates_today: null },
  linkHealth: { checked: 0, alive: null, last_checked_at: null },
  storage: { cached_bytes: null, drivers: [] },
  channels: [
    { id: "example-a", name: "示例 · 4K 频道", messages: 128, last_received_at: "10:14:02" },
    { id: "example-b", name: "示例 · 剧集频道", messages: 76, last_received_at: "09:42:18" },
  ],
  logs: [
    { id: "demo-1", time: "10:14:02", type: "success", source: "示例 · 4K 频道", summary: "提取 115 链接 → 识别《沙丘 2》 → TMDB 匹配", raw: { message: "沙丘 2 (2024) https://115.com/s/example", channel: "示例 · 4K 频道" }, parsed: { title: "沙丘 2", year: 2024, provider: "115", tmdb_id: 693134, status: "matched" } },
    { id: "demo-2", time: "10:15:30", type: "ignored", source: "示例 · 剧集频道", summary: "未检测到支持的网盘链接，已忽略", raw: { message: "周末观影推荐", channel: "示例 · 剧集频道" }, parsed: { links: [], status: "ignored" } },
    { id: "demo-3", time: "10:16:11", type: "duplicate", source: "示例 · 4K 频道", summary: "《奥本海默》已有作品，合并新来源", raw: { message: "奥本海默 (2023) https://115.com/s/example2", channel: "示例 · 4K 频道" }, parsed: { title: "奥本海默", year: 2023, provider: "115", status: "merged" } },
    { id: "demo-4", time: "10:17:05", type: "error", source: "示例 · 剧集频道", summary: "链接格式异常，等待人工核对", raw: { message: "某剧资源：链接缺失", channel: "示例 · 剧集频道" }, parsed: { status: "error", reason: "invalid_url" } },
  ],
  queues: [
    { id: "cleaning", label: "待清洗与 TMDB 刮削", pending: null, completed: null, total: null, state: "unavailable" },
    { id: "health", label: "网盘链接巡检", pending: null, completed: null, total: null, state: "unavailable" },
    { id: "transfer", label: "离线缓存 / 转存", pending: null, completed: null, total: null, state: "not_implemented" },
  ],
  trends: Array.from({ length: 30 }, (_, i) => ({ day: `第 ${i + 1} 天`, received: Math.round(42 + i * 1.5 + 18 * Math.sin(i * 0.85)), imported: Math.round(19 + i + 10 * Math.sin(i * 0.85 + 0.4)) })),
  distribution: [
    { label: "115 网盘", value: 57, color: "#087f7b" },
    { label: "夸克", value: 20, color: "#287db0" },
    { label: "阿里云盘", value: 12, color: "#58bda5" },
    { label: "百度网盘", value: 7, color: "#d9a056" },
    { label: "ED2K / 磁力", value: 4, color: "#9caab1" },
  ],
};
