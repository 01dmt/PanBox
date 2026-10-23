import { useState } from "react";
import {
  Film,
  FolderOpen,
  LayoutGrid,
  List,
  RotateCcw,
  Search,
  SlidersHorizontal,
  UserRound,
  X,
} from "lucide-react";
import { formatNumber } from "../lib/format";

const CLASS_OPTIONS = [
  ["all", "全部类型"],
  ["media", "影视"],
  ["collection", "资源包"],
  ["music", "音乐"],
  ["person", "人物"],
  ["other", "其他"],
];

const AVAILABILITY = [
  ["all", "全部状态"],
  ["available", "可用来源"],
  ["pending", "待检查"],
  ["invalid", "失效来源"],
  ["unknown", "状态未知"],
];

const TMDB = [
  ["all", "全部识别状态"],
  ["matched", "已匹配 TMDB"],
  ["unrecognized", "尚未匹配"],
  ["review", "待确认"],
  ["not_found", "未找到"],
  ["error", "匹配错误"],
];

const PRIMARY_FILTERS = ["provider", "availability", "tmdb_status"];
const ADVANCED_FILTERS = ["year", "quality", "tag", "channel"];

const GROUPS = [
  { id: "all", label: "全部", countKey: "total", Icon: LayoutGrid },
  { id: "media", label: "影视资源", countKey: "media", Icon: Film },
  { id: "person", label: "人物", countKey: "person", Icon: UserRound },
  { id: "series", label: "系列", countKey: "series", Icon: FolderOpen },
  { id: "unrecognized", label: "未识别", countKey: "unrecognized", Icon: Search },
];

function Select({ label, name, value, options, onChange }) {
  return (
    <label className="filter-control">
      <span>{label}</span>
      <select
        value={value || "all"}
        onChange={(event) => onChange({ [name]: event.target.value })}
      >
        {options.map(([key, text]) => (
          <option value={key} key={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ResourceFilterToolbar({
  filters,
  options,
  total,
  summary,
  loading,
  search = "",
  onSearch,
  layoutMode = "table",
  onLayoutModeChange,
  onChange,
  onReset,
}) {
  const [expanded, setExpanded] = useState(false);

  const providers = [
    ["all", "全部网盘"],
    ...(options?.providers || []).map((provider) => [provider.id, provider.label]),
  ];
  const years = [
    ["all", "全部年份"],
    ["unknown", "年份未知"],
    ...(options?.years || []).map((year) => [String(year), `${year} 年`]),
  ];
  const qualities = [
    ["all", "全部清晰度"],
    ["2160P", "4K / 2160P"],
    ["1080P", "1080P"],
    ["1080I", "1080I"],
    ["720P", "720P"],
    ["480P", "480P"],
    ["unknown", "未识别"],
  ];
  const channels = [
    ["all", "全部频道"],
    ...(options?.channels || []).map((channel) => [channel, channel]),
  ];
  const tags = [
    ["all", "全部标签"],
    ...(options?.tags || []).map((tag) => [tag, tag]),
  ];

  const choices = {
    provider: providers,
    resource_class: CLASS_OPTIONS,
    availability: AVAILABILITY,
    tmdb_status: TMDB,
    year: years,
    quality: qualities,
    tag: tags,
    channel: channels,
  };

  const labels = {
    resource_class: "类型",
    provider: "网盘",
    availability: "状态",
    tmdb_status: "TMDB",
    year: "年份",
    quality: "清晰度",
    tag: "标签",
    channel: "频道",
  };

  const active = Object.entries(filters).filter(
    ([key, value]) =>
      !["group", "page", "pageSize", "sort"].includes(key) &&
      value &&
      value !== "all"
  );
  const advancedCount = ADVANCED_FILTERS.filter(
    (key) => filters[key] && filters[key] !== "all"
  ).length;

  const selectGroup = (group) =>
    onChange({ group, resource_class: "all", tmdb_status: "all", page: 1 });

  const count = (key, fallback = 0) =>
    Number.isFinite(Number(summary?.[key])) ? summary[key] : fallback;

  const hasSearch = Boolean(search && search.trim());
  const canReset = active.length > 0 || hasSearch;

  return (
    <section className="resource-filter-toolbar" aria-label="资源库分类与筛选">
      {/* Top row: RE0-style Category Tabs + Compact metrics pills */}
      <div className="resource-top-bar">
        <div className="resource-category-tabs" role="tablist" aria-label="资源分类">
          {GROUPS.map(({ id, label, countKey, Icon }) => {
            const selected = filters.group === id;
            const amount = formatNumber(count(countKey, id === "all" ? total : 0));
            return (
              <button
                key={id}
                id={`resource-tab-${id}`}
                type="button"
                role="tab"
                className={`resource-tab-btn ${selected ? "active" : ""}`}
                aria-selected={selected}
                aria-controls="resource-category-panel"
                onClick={() => selectGroup(id)}
                aria-label={`${label}，${amount} 条来源`}
              >
                <Icon size={16} aria-hidden="true" />
                <span>{label}</span>
                <b className={`resource-tab-badge ${id === "unrecognized" ? "unrecognized" : ""}`}>
                  {amount}
                </b>
              </button>
            );
          })}
        </div>

        {summary && (
          <div className="resource-header-metrics" aria-label="来源可用性状态概览">
            <span className="resource-metric-pill available" title="当前可用来源数">
              <i aria-hidden="true" />
              <span>可用 {formatNumber(summary.available || 0)}</span>
            </span>
            {Number(summary.pending) > 0 && (
              <span className="resource-metric-pill pending" title="待检查来源数">
                <i aria-hidden="true" />
                <span>待查 {formatNumber(summary.pending)}</span>
              </span>
            )}
            {Number(summary.invalid) > 0 && (
              <span className="resource-metric-pill invalid" title="已失效来源数">
                <i aria-hidden="true" />
                <span>失效 {formatNumber(summary.invalid)}</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Primary filters row with integrated search box */}
      <div className="resource-filter-row">
        {/* Integrated search box */}
        <form
          className="resource-search-box"
          role="search"
          onSubmit={(e) => e.preventDefault()}
        >
          <Search size={14} aria-hidden="true" />
          <input
            value={search}
            onChange={(e) => onSearch?.(e.target.value)}
            placeholder="搜索片名、文件名、原始消息..."
            aria-label="搜索资源库"
          />
          {hasSearch && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={() => onSearch?.("")}
              title="清空搜索"
              aria-label="清空搜索"
            >
              <X size={13} />
            </button>
          )}
        </form>

        {PRIMARY_FILTERS.map((key) => (
          <Select
            key={key}
            label={labels[key]}
            name={key}
            value={filters[key]}
            options={choices[key]}
            onChange={onChange}
          />
        ))}

        <div className="resource-filter-tools">
          <label className="filter-control resource-sort">
            <span>排序</span>
            <select
              value={filters.sort}
              onChange={(event) => onChange({ sort: event.target.value })}
            >
              <option value="updated_desc">最近更新</option>
              <option value="created_desc">最近入库</option>
              <option value="checked_desc">最近检查</option>
              <option value="title_asc">标题排序</option>
            </select>
          </label>

          <button
            type="button"
            className={`button small filter-expand ${expanded ? "active" : ""}`}
            aria-expanded={expanded}
            aria-controls="advanced-resource-filters"
            onClick={() => setExpanded((value) => !value)}
          >
            <SlidersHorizontal size={14} />
            <span>更多筛选</span>
            {advancedCount ? <b>{advancedCount}</b> : null}
          </button>

          {/* Density / View mode switch: High-density Table (Default) vs Grid Cards */}
          <div className="resource-view-mode-toggle" aria-label="排版密度切换">
            <button
              type="button"
              className={`mode-toggle-btn ${layoutMode === "table" ? "active" : ""}`}
              onClick={() => onLayoutModeChange?.("table")}
              title="紧凑清单 (高信息密度，一屏看全)"
              aria-label="紧凑清单模式"
            >
              <List size={15} />
            </button>
            <button
              type="button"
              className={`mode-toggle-btn ${layoutMode === "grid" ? "active" : ""}`}
              onClick={() => onLayoutModeChange?.("grid")}
              title="资产卡片"
              aria-label="资产卡片模式"
            >
              <LayoutGrid size={15} />
            </button>
          </div>

          <button
            type="button"
            className="icon-only resource-reset-btn"
            title="清除全部筛选"
            aria-label="清除全部筛选"
            disabled={!canReset && !loading}
            onClick={onReset}
          >
            <RotateCcw size={15} />
          </button>
        </div>
      </div>

      {/* Advanced filters (collapsible) */}
      {expanded ? (
        <div className="resource-filter-advanced" id="advanced-resource-filters">
          {ADVANCED_FILTERS.map((key) => (
            <Select
              key={key}
              label={labels[key]}
              name={key}
              value={filters[key]}
              options={choices[key]}
              onChange={onChange}
            />
          ))}
        </div>
      ) : null}

      {/* Active filter chips */}
      {active.length || hasSearch ? (
        <ul className="active-filters" aria-label="当前资源筛选条件">
          {hasSearch && (
            <li>
              <span>搜索：{search.trim()}</span>
              <button
                type="button"
                className="icon-only"
                aria-label="移除搜索条件"
                onClick={() => onSearch?.("")}
              >
                ×
              </button>
            </li>
          )}
          {active.map(([key, value]) => {
            const label = choices[key]?.find(([id]) => id === value)?.[1] || value;
            return (
              <li key={key}>
                <span>
                  {labels[key] || key}：{label}
                </span>
                <button
                  type="button"
                  className="icon-only"
                  aria-label={`移除${labels[key] || key}筛选`}
                  onClick={() => onChange({ [key]: "all" })}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
