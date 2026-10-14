import { Film, FolderOpen, LayoutGrid, RotateCcw, Search, UserRound } from "lucide-react";
import { formatNumber } from "../lib/format";

const CLASS_OPTIONS = [["all", "全部类型"], ["media", "影视"], ["collection", "资源包"], ["music", "音乐"], ["person", "人物"], ["other", "其他"]];
const AVAILABILITY = [["all", "全部状态"], ["available", "可用来源"], ["pending", "待检查"], ["invalid", "失效来源"], ["unknown", "状态未知"]];
const TMDB = [["all", "全部识别状态"], ["matched", "已匹配 TMDB"], ["unrecognized", "尚未匹配"], ["review", "待确认"], ["not_found", "未找到"], ["error", "匹配错误"]];
const GROUPS = [
  { id: "all", label: "全部", countKey: "total", Icon: LayoutGrid },
  { id: "media", label: "影视资源", countKey: "media", Icon: Film },
  { id: "person", label: "人物", countKey: "person", Icon: UserRound },
  { id: "series", label: "系列", countKey: "series", Icon: FolderOpen },
  { id: "unrecognized", label: "未识别", countKey: "unrecognized", Icon: Search },
];

function navigateTabs(event) {
  const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
  if (!step && event.key !== "Home" && event.key !== "End") return;
  event.preventDefault();
  const tabs = Array.from(event.currentTarget.parentElement.querySelectorAll('[role="tab"]'));
  const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (tabs.indexOf(event.currentTarget) + step + tabs.length) % tabs.length;
  tabs[index].focus();
  tabs[index].click();
}

function Select({ label, name, value, options, onChange }) {
  return <label className="filter-control"><span>{label}</span><select value={value || "all"} onChange={(event) => onChange({ [name]: event.target.value })}>{options.map(([key, text]) => <option value={key} key={key}>{text}</option>)}</select></label>;
}

export default function ResourceFilterToolbar({ filters, options, total, summary, loading, onChange, onReset }) {
  const providers = [["all", "全部网盘"], ...(options?.providers || []).map((provider) => [provider.id, provider.label])];
  const years = [["all", "全部年份"], ["unknown", "年份未知"], ...(options?.years || []).map((year) => [String(year), `${year} 年`])];
  const qualities = [["all", "全部清晰度"], ["2160P", "4K / 2160P"], ["1080P", "1080P"], ["1080I", "1080I"], ["720P", "720P"], ["480P", "480P"], ["unknown", "未识别"]];
  const channels = [["all", "全部频道"], ...(options?.channels || []).map((channel) => [channel, channel])];
  const tags = [["all", "全部标签"], ...(options?.tags || []).map((tag) => [tag, tag])];
  const active = Object.entries(filters).filter(([key, value]) => !["group", "page", "pageSize", "sort"].includes(key) && value && value !== "all");
  const selectGroup = (group) => onChange({ group, resource_class: "all", tmdb_status: "all", page: 1 });
  const count = (key, fallback = 0) => Number.isFinite(Number(summary?.[key])) ? summary[key] : fallback;
  return <section className="resource-filter-toolbar" aria-label="资源筛选">
    <div className="resource-category-tabs" role="tablist" aria-label="资源分类">
      {GROUPS.map(({ id, label, countKey, Icon }) => {
        const selected = filters.group === id;
        const amount = formatNumber(count(countKey, id === "all" ? total : 0));
        return <button key={id} id={`resource-tab-${id}`} type="button" role="tab"
          className={selected ? "active" : ""} aria-selected={selected} aria-controls="resource-category-panel"
          tabIndex={selected ? 0 : -1} onKeyDown={navigateTabs} onClick={() => selectGroup(id)}
          aria-label={`${label}，${amount} 条来源`} aria-describedby={id === "unrecognized" ? "resource-unrecognized-tip" : undefined}>
          <Icon size={18} aria-hidden="true" /><span>{label}</span>
          <b className={id === "unrecognized" ? "resource-tab-bubble" : ""}>{amount}</b>
          {id === "unrecognized" ? <span className="resource-category-tooltip" id="resource-unrecognized-tip" role="tooltip">
            {amount} 条未识别来源<br />包含待确认、未找到和匹配错误，排除人物与系列。
          </span> : null}
        </button>;
      })}
    </div>
    <div className="resource-filter-row"><Select label="网盘" name="provider" value={filters.provider} options={providers} onChange={onChange} /><Select label="资源类型" name="resource_class" value={filters.resource_class} options={CLASS_OPTIONS} onChange={onChange} /><Select label="来源状态" name="availability" value={filters.availability} options={AVAILABILITY} onChange={onChange} /><Select label="TMDB 状态" name="tmdb_status" value={filters.tmdb_status} options={TMDB} onChange={onChange} /><Select label="年份" name="year" value={filters.year} options={years} onChange={onChange} /><Select label="清晰度" name="quality" value={filters.quality} options={qualities} onChange={onChange} /><Select label="标签" name="tag" value={filters.tag} options={tags} onChange={onChange} /><Select label="频道" name="channel" value={filters.channel} options={channels} onChange={onChange} /><label className="filter-control resource-sort"><span>排序</span><select value={filters.sort} onChange={(event) => onChange({ sort: event.target.value })}><option value="updated_desc">最近更新</option><option value="created_desc">最近入库</option><option value="checked_desc">最近检查</option><option value="title_asc">标题排序</option></select></label><button type="button" className="icon-only" title="清除资源筛选" aria-label="清除资源筛选" disabled={!active.length && !loading} onClick={onReset}><RotateCcw size={16} /></button></div>
    {active.length ? <ul className="active-filters" aria-label="当前资源筛选条件">{active.map(([key, value]) => {
      const labels = { resource_class: CLASS_OPTIONS, provider: providers, availability: AVAILABILITY, tmdb_status: TMDB, year: years, quality: qualities, tag: tags, channel: channels };
      const label = labels[key]?.find(([id]) => id === value)?.[1] || value;
      return <li key={key}><span>{key === "resource_class" ? "类型" : key === "provider" ? "网盘" : key === "availability" ? "状态" : key === "tmdb_status" ? "TMDB" : key === "year" ? "年份" : key === "quality" ? "清晰度" : key === "tag" ? "标签" : key === "channel" ? "频道" : key}：{label}</span><button type="button" className="icon-only" aria-label={`移除${key}筛选`} onClick={() => onChange({ [key]: "all" })}>×</button></li>;
    })}</ul> : null}
  </section>;
}
